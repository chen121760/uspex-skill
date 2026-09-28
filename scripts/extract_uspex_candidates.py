#!/usr/bin/env python3
"""Package the structures of a USPEX run that satisfy explicit conditions.

The user's question is usually "give me the structures that ...", where the
condition can be geometric (composition, atom count, space group, volume),
energetic (enthalpy per atom, hull distance, per-cell enthalpy), provenance
(origin, generation, Pareto front) or convergence-related. This tool turns such
a sentence into a reproducible selection, writes the structures out, and records
the evidence that selected each one.

Design rules that come from real failures rather than taste:

* Conditions are parsed, never ``eval``-ed, and every field is bound to exactly
  one source file so the manifest can say where a value came from.
* Row parsing is bracket aware: ``Composition`` and ``KPOINTS`` are padded like
  ``[   1  0  5 ]``, so whitespace splitting silently misaligns every column.
* Block parsing uses two independent boundaries -- the arithmetic implied by the
  element counts and the position of the next ``EA`` header -- and refuses the
  file when they disagree. One boundary alone corrupts the structures after the
  first bad block.
* A value that is missing for a structure is never treated as zero. Such
  structures are excluded and counted as ``missing_field`` so an empty result is
  explainable.
* ``Individuals`` IDs repeat across generations, so joining it to structures
  requires an explicit ``generation=`` condition; otherwise the tool refuses.

Exit codes: 0 wrote at least one structure, 3 nothing matched (not an error),
2 the request or the data is wrong.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import shutil
import sys
import tarfile
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_EMPTY = 3

SCHEMA_VERSION = 1

EA_HEADER_RE = re.compile(r"^EA(\d+)\s")
NUMBER_HEADER_RE = re.compile(r"^number=(\d+)\b")
SYM_RE = re.compile(r"Sym\.group:\s*(\d+)")
ELEMENT_RE = re.compile(r"^[A-Z][a-z]?$")
SENTINEL_ENERGY = 999999.0

KNOWN_TABLE_SOURCES = {
    "pareto": ("Pareto_ranking", 2),
    "individuals": ("Individuals", 2),
    "hull": ("extended_convex_hull", 2),
}
POSCAR_SOURCES = {"relaxed": "gatheredPOSCARS", "unrelaxed": "gatheredPOSCARS_unrelaxed"}


# --------------------------------------------------------------------------- #
# Tokenising
# --------------------------------------------------------------------------- #


def tokenize_row(line: str) -> list[str]:
    """Split a USPEX table row, keeping ``[...]`` spans as single tokens."""
    tokens: list[str] = []
    current: list[str] = []
    depth = 0
    for char in line.strip():
        if char == "[":
            depth += 1
            current.append(char)
            continue
        if char == "]":
            depth = max(0, depth - 1)
            current.append(char)
            continue
        if char.isspace() and depth == 0:
            if current:
                tokens.append("".join(current))
                current = []
            continue
        current.append(char)
    if current:
        tokens.append("".join(current))
    return tokens


def parse_bracket_numbers(token: str) -> list[float]:
    if not token.startswith("["):
        return []
    inner = token.strip("[]").strip()
    if not inner:
        return []
    values = []
    for part in inner.split():
        try:
            values.append(float(part))
        except ValueError:
            return []
    return values


@dataclass
class Table:
    name: str
    path: Path
    columns: list[str]
    rows: list[dict[str, str]]

    def by_id(self) -> dict[int, dict[str, str]]:
        """Index by ID once; a 40-generation run has tens of thousands of blocks."""
        cache = getattr(self, "_by_id_cache", None)
        if cache is not None:
            return cache
        mapping: dict[int, dict[str, str]] = {}
        for row in self.rows:
            raw = row.get("ID") or row.get("id")
            if raw is None:
                continue
            try:
                mapping[int(float(raw))] = row
            except ValueError:
                continue
        object.__setattr__(self, "_by_id_cache", mapping)
        return mapping

    def rows_by_id(self) -> dict[int, list[dict[str, str]]]:
        """Index all rows per ID; Individuals repeats IDs across generations."""
        cache = getattr(self, "_rows_by_id_cache", None)
        if cache is not None:
            return cache
        mapping: dict[int, list[dict[str, str]]] = {}
        for row in self.rows:
            raw = row.get("ID") or row.get("id")
            if raw is None:
                continue
            try:
                key = int(float(raw))
            except ValueError:
                continue
            mapping.setdefault(key, []).append(row)
        object.__setattr__(self, "_rows_by_id_cache", mapping)
        return mapping


def parse_table(text: str, name: str, path: Path, header_rows: int) -> Table:
    """Parse a USPEX table whose first line is the column header and second the units."""
    lines = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if len(lines) < header_rows:
        return Table(name=name, path=path, columns=[], rows=[])
    columns = tokenize_row(lines[0])
    rows: list[dict[str, str]] = []
    for line in lines[header_rows:]:
        tokens = tokenize_row(line)
        if not tokens or not tokens[0][0].isdigit():
            continue
        # Rows can carry trailing blanks; keep index alignment with the header.
        row = {columns[i]: tokens[i] for i in range(min(len(columns), len(tokens)))}
        rows.append(row)
    return Table(name=name, path=path, columns=columns, rows=rows)


# --------------------------------------------------------------------------- #
# POSCAR blocks
# --------------------------------------------------------------------------- #


@dataclass
class Block:
    id: int
    header: str
    lines: list[str]
    elements: list[str]
    counts: list[int]
    source: str
    spacegroup: int | None = None
    scale: float | None = None
    lattice: list[list[float]] = dataclass_field(default_factory=list)
    lattice_params: tuple[float, float, float, float, float, float] | None = None
    coord_type: str | None = None
    volume: float | None = None
    boundary: str = "arithmetic"  # "arithmetic" | "header" | "eof"

    @property
    def natoms(self) -> int:
        return sum(self.counts)

    @property
    def nelements(self) -> int:
        return sum(1 for count in self.counts if count > 0)

    @property
    def formula(self) -> str:
        return "".join(
            element if count == 1 else f"{element}{count}"
            for element, count in zip(self.elements, self.counts)
        )


def _vector_length(vector: list[float]) -> float:
    return math.sqrt(sum(value * value for value in vector))


def _angle(a: list[float], b: list[float]) -> float:
    denominator = _vector_length(a) * _vector_length(b)
    if denominator == 0:
        return 0.0
    cosine = max(-1.0, min(1.0, sum(x * y for x, y in zip(a, b)) / denominator))
    return math.degrees(math.acos(cosine))


def _volume(vectors: list[list[float]]) -> float:
    (a, b, c) = vectors
    return abs(
        a[0] * (b[1] * c[2] - b[2] * c[1])
        - a[1] * (b[0] * c[2] - b[2] * c[0])
        + a[2] * (b[0] * c[1] - b[1] * c[0])
    )


class ParseFailure(RuntimeError):
    pass


def parse_poscar_blocks(text: str, source: str, strict: bool = True) -> tuple[list[Block], list[str]]:
    """Return (blocks, problems). Problems are fatal in strict mode."""
    lines = text.splitlines()
    header_indices: list[tuple[int, int]] = []
    for index, line in enumerate(lines):
        match = EA_HEADER_RE.match(line)
        if match:
            header_indices.append((index, int(match.group(1))))
            continue
        match = NUMBER_HEADER_RE.match(line)
        if match:
            header_indices.append((index, int(match.group(1))))

    blocks: list[Block] = []
    problems: list[str] = []
    seen: dict[tuple[str, int], int] = {}
    for position, (start, block_id) in enumerate(header_indices):
        end_of_file = header_indices[position + 1][0] if position + 1 < len(header_indices) else len(lines)
        block = _parse_one_block(lines, start, end_of_file, block_id, source, problems)
        if block is None:
            continue
        key = (source, block.id)
        if key in seen:
            problems.append(
                f"duplicate EA id {block.id} in {source} (lines {seen[key] + 1} and {start + 1}); "
                "refusing to overwrite one structure with another"
            )
        seen.setdefault(key, start)
        blocks.append(block)
    if problems and strict:
        raise ParseFailure("; ".join(problems))
    return blocks, problems


def _parse_one_block(
    lines: list[str], start: int, end: int, block_id: int, source: str, problems: list[str]
) -> Block | None:
    window = lines[start:end]
    if len(window) < 8:
        problems.append(f"EA{block_id}: block is too short to be a VASP-5 POSCAR")
        return None
    header = lines[start]
    match = SYM_RE.search(header)
    spacegroup = int(match.group(1)) if match else None

    try:
        scale = float(window[1].split()[0])
    except (IndexError, ValueError):
        problems.append(f"EA{block_id}: unreadable scale factor on the line after the header")
        return None
    try:
        vectors = [[float(value) * scale for value in window[i].split()[:3]] for i in (2, 3, 4)]
    except (IndexError, ValueError):
        problems.append(f"EA{block_id}: unreadable lattice vectors")
        return None
    if any(len(vector) != 3 for vector in vectors):
        problems.append(f"EA{block_id}: lattice vector with fewer than three components")
        return None

    elements: list[str] = []
    counts: list[int] = []
    element_index: int | None = None
    for index in range(5, min(len(window), 10)):
        tokens = window[index].split()
        if tokens and all(ELEMENT_RE.match(token) for token in tokens):
            if index + 1 >= len(window):
                break
            count_tokens = window[index + 1].split()
            if len(count_tokens) != len(tokens):
                continue
            try:
                parsed = [int(float(token)) for token in count_tokens]
            except ValueError:
                continue
            if all(value > 0 for value in parsed):
                elements, counts, element_index = tokens, parsed, index
                break
    if element_index is None:
        problems.append(f"EA{block_id}: could not find a VASP-5 element/count pair")
        return None

    coord_type = None
    coord_index = None
    for index in range(element_index + 2, min(len(window), element_index + 4)):
        token = window[index].strip().split()[0] if window[index].strip() else ""
        if token[:1].upper() in ("D", "C") or token.lower().startswith("selective"):
            coord_type = window[index].strip()
            coord_index = index
            if token.lower().startswith("selective"):
                continue
            break
    if coord_index is None:
        problems.append(f"EA{block_id}: no Direct/Cartesian coordinate line")
        return None

    total_atoms = sum(counts)
    arithmetic_end = coord_index + 1 + total_atoms
    body = window[:arithmetic_end]
    if len(body) < arithmetic_end:
        problems.append(
            f"EA{block_id}: {total_atoms} atoms declared but only "
            f"{max(0, len(window) - coord_index - 1)} coordinate lines follow"
        )
        return None

    boundary = "arithmetic"
    if arithmetic_end == len(window):
        boundary = "header" if end < len(lines) else "eof"
    else:
        # The arithmetic boundary disagrees with the next header position.
        trimmed = arithmetic_end
        if len(window) > trimmed and window[trimmed].strip():
            problems.append(
                f"EA{block_id}: arithmetic block length ({arithmetic_end}) does not land on the next "
                f"header; found {window[trimmed].strip()[:40]!r} instead"
            )
            return None
        body = window[:trimmed]

    lattice_params = None
    numbers = header
    for pattern in (EA_HEADER_RE, NUMBER_HEADER_RE):
        match = pattern.match(numbers)
        if match:
            numbers = numbers[match.end():]
            break
    numbers = SYM_RE.sub("", numbers).strip()
    values: list[float] = []
    for token in numbers.split():
        try:
            values.append(float(token))
        except ValueError:
            values = []
            break
    if len(values) >= 6:
        lattice_params = (values[0], values[1], values[2], values[3], values[4], values[5])

    return Block(
        id=block_id,
        header=header,
        lines=body,
        elements=elements,
        counts=counts,
        source=source,
        spacegroup=spacegroup,
        scale=scale,
        lattice=vectors,
        lattice_params=lattice_params,
        coord_type=coord_type,
        volume=_volume(vectors),
        boundary=boundary,
    )


# --------------------------------------------------------------------------- #
# Field registry
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FieldSpec:
    name: str
    kind: str  # int | float | str | bool
    source: str
    unit: str
    help: str


FIELD_SPECS: tuple[FieldSpec, ...] = (
    FieldSpec("id", "int", "geometry", "-", "USPEX structure id (EA<id>)"),
    FieldSpec("natoms", "int", "geometry", "-", "atoms per cell"),
    FieldSpec("nelements", "int", "geometry", "-", "populated element types"),
    FieldSpec("formula", "str", "geometry", "-", "formula from the POSCAR block"),
    FieldSpec("element", "list", "geometry", "-", "elements present; use 'element=Li' or 'element in Li,Mg'"),
    FieldSpec("spacegroup", "int", "geometry", "-", "Sym.group number in the block header"),
    FieldSpec("volume", "float", "geometry", "A^3", "cell volume from the lattice vectors"),
    FieldSpec("a", "float", "geometry", "A", "lattice parameter a"),
    FieldSpec("b", "float", "geometry", "A", "lattice parameter b"),
    FieldSpec("c", "float", "geometry", "A", "lattice parameter c"),
    FieldSpec("alpha", "float", "geometry", "deg", "lattice angle alpha"),
    FieldSpec("beta", "float", "geometry", "deg", "lattice angle beta"),
    FieldSpec("gamma", "float", "geometry", "deg", "lattice angle gamma"),
    FieldSpec("source", "str", "geometry", "-", "relaxed | unrelaxed"),
    FieldSpec("boundary", "str", "geometry", "-", "how the block end was determined"),
    FieldSpec("front", "int", "pareto", "-", "Pareto front number from Pareto_ranking"),
    FieldSpec("enthalpy_atom", "float", "pareto", "eV/atom", "enthalpy per atom"),
    FieldSpec("hull", "float", "pareto", "eV/block", "ConvexHull column of Pareto_ranking"),
    FieldSpec("objective", "float", "pareto", "-", "value of the variable objective column"),
    FieldSpec("origin", "str", "pareto", "-", "Random | Heredity | TransMutate | keptBest ..."),
    FieldSpec("generation", "int", "individuals", "-", "generation from Individuals"),
    FieldSpec("enthalpy_cell", "float", "individuals", "eV/cell", "enthalpy per cell"),
    FieldSpec("density", "float", "individuals", "g/cm^3", "density"),
    FieldSpec("objective_ind", "float", "individuals", "-", "variable objective in Individuals"),
    FieldSpec("hull_block", "float", "hull", "eV/block", "distance to the convex hull"),
    FieldSpec("enthalpy_atom_hull", "float", "hull", "eV/atom", "enthalpy per atom in the hull file"),
    FieldSpec("y_axis", "float", "hull", "eV/atom", "formation energy axis of the hull file"),
    FieldSpec("converged", "bool", "calcfold", "-", "energy.txt present, finite and below the sentinel"),
    FieldSpec("energy_cell", "float", "calcfold", "eV/cell", "per-cell energy of the current generation"),
)
FIELD_BY_NAME = {spec.name: spec for spec in FIELD_SPECS}
NUMERIC_KINDS = {"int", "float"}


# --------------------------------------------------------------------------- #
# Predicate language
# --------------------------------------------------------------------------- #

TOKEN_RE = re.compile(r"==|!=|<=|>=|[=<>~()]|[^\s()=<>~!]+")


class PredicateError(ValueError):
    pass


@dataclass
class Node:
    op: str
    field: str | None = None
    values: list[str] = dataclass_field(default_factory=list)
    children: list["Node"] = dataclass_field(default_factory=list)


def parse_predicate(text: str) -> Node:
    tokens = TOKEN_RE.findall(text)
    if not tokens:
        raise PredicateError("empty condition")
    position = 0

    def peek() -> str | None:
        return tokens[position] if position < len(tokens) else None

    def take() -> str:
        nonlocal position
        if position >= len(tokens):
            raise PredicateError(f"unexpected end of condition: {text!r}")
        token = tokens[position]
        position += 1
        return token

    def parse_or() -> Node:
        node = parse_and()
        while (peek() or "").lower() == "or":
            take()
            node = Node("or", children=[node, parse_and()])
        return node

    def parse_and() -> Node:
        node = parse_not()
        while (peek() or "").lower() == "and":
            take()
            node = Node("and", children=[node, parse_not()])
        return node

    def parse_not() -> Node:
        if (peek() or "").lower() == "not":
            take()
            return Node("not", children=[parse_not()])
        return parse_atom()

    def parse_atom() -> Node:
        token = peek()
        if token == "(":
            take()
            node = parse_or()
            if peek() != ")":
                raise PredicateError("unbalanced parentheses")
            take()
            return node
        name = take()
        if name not in FIELD_BY_NAME:
            known = ", ".join(sorted(FIELD_BY_NAME))
            raise PredicateError(f"unknown field {name!r}; available fields: {known}")
        operator = (peek() or "").lower()
        if operator == "in":
            take()
            raw = take()
            values = [item for item in raw.split(",") if item != ""]
            if not values:
                raise PredicateError(f"'in' needs a comma separated list, got {raw!r}")
            return Node("in", field=name, values=values)
        if operator not in ("=", "==", "!=", "<", "<=", ">", ">=", "~"):
            raise PredicateError(f"field {name!r} needs an operator, got {operator!r}")
        take()
        value = take()
        if operator in ("=", "==") and ".." in value:
            return Node("range", field=name, values=value.split("..", 1))
        return Node(operator, field=name, values=[value])

    node = parse_or()
    if position != len(tokens):
        raise PredicateError(f"unexpected token {tokens[position]!r} in {text!r}")
    return node


def node_fields(node: Node) -> set[str]:
    names = {node.field} if node.field else set()
    for child in node.children:
        names |= node_fields(child)
    return names


def node_sources(node: Node) -> set[str]:
    sources = set()
    if node.field:
        sources.add(FIELD_BY_NAME[node.field].source)
    for child in node.children:
        sources |= node_sources(child)
    return sources


def render_predicate(node: Node) -> str:
    if node.op in ("and", "or"):
        return "(" + f" {node.op} ".join(render_predicate(child) for child in node.children) + ")"
    if node.op == "not":
        return f"not ({render_predicate(node.children[0])})"
    if node.op == "in":
        return f"{node.field} in {','.join(node.values)}"
    if node.op == "range":
        return f"{node.field} = {node.values[0]}..{node.values[-1]}"
    return f"{node.field} {node.op} {node.values[0]}"


def _as_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def evaluate(node: Node, values: dict[str, object]) -> bool | None:
    """Return True/False, or None when a referenced field is missing.

    None is propagated rather than coerced to False so that a structure without
    evidence is excluded *and reported*, instead of silently counting as "does
    not match the condition".
    """
    if node.op == "and":
        results = [evaluate(child, values) for child in node.children]
        if any(result is False for result in results):
            return False
        if any(result is None for result in results):
            return None
        return True
    if node.op == "or":
        results = [evaluate(child, values) for child in node.children]
        if any(result is True for result in results):
            return True
        if any(result is None for result in results):
            return None
        return False
    if node.op == "not":
        inner = evaluate(node.children[0], values)
        return None if inner is None else (not inner)

    assert node.field is not None
    spec = FIELD_BY_NAME[node.field]
    current = values.get(node.field)
    if current is None:
        return None

    if spec.kind == "list":
        items = [str(item) for item in current]  # type: ignore[union-attr]
        if node.op == "in":
            return any(value in items for value in node.values)
        if node.op in ("=", "=="):
            return node.values[0] in items
        if node.op == "!=":
            return node.values[0] not in items
        raise PredicateError(f"operator {node.op!r} is not supported for field {node.field}")

    if node.op == "in":
        if spec.kind == "str":
            return str(current) in node.values
        number = _as_float(current)
        if number is None:
            return None
        wanted = {_as_float(item) for item in node.values}
        return number in {item for item in wanted if item is not None}
    if node.op == "range":
        if spec.kind not in NUMERIC_KINDS:
            raise PredicateError(f"range comparison needs a numeric field, {node.field} is {spec.kind}")
        number = _as_float(current)
        if number is None:
            return None
        low_raw, high_raw = node.values[0], node.values[-1]
        low = _as_float(low_raw) if low_raw else None
        high = _as_float(high_raw) if high_raw else None
        if low is None and high is None:
            raise PredicateError(f"range {low_raw}..{high_raw} has no bound")
        if low is not None and number < low:
            return False
        if high is not None and number > high:
            return False
        return True
    if node.op == "~":
        pattern = node.values[0]
        try:
            return re.search(pattern, str(current)) is not None
        except re.error as exc:
            raise PredicateError(f"invalid pattern {pattern!r}: {exc}") from exc

    target = node.values[0]
    if spec.kind in NUMERIC_KINDS:
        number = _as_float(current)
        wanted = _as_float(target)
        if number is None:
            return None
        if wanted is None:
            raise PredicateError(f"{node.field} is numeric; {target!r} is not a number")
        comparisons = {
            "=": lambda a, b: a == b,
            "==": lambda a, b: a == b,
            "!=": lambda a, b: a != b,
            "<": lambda a, b: a < b,
            "<=": lambda a, b: a <= b,
            ">": lambda a, b: a > b,
            ">=": lambda a, b: a >= b,
        }
        return comparisons[node.op](number, wanted)
    if spec.kind == "bool":
        wanted = target.strip().lower() in ("1", "true", "yes", "on")
        current_bool = bool(current)
        return current_bool != wanted if node.op == "!=" else current_bool == wanted
    text = str(current)
    if node.op in ("=", "=="):
        return text == target
    if node.op == "!=":
        return text != target
    raise PredicateError(f"operator {node.op!r} is not supported for text field {node.field}")


# --------------------------------------------------------------------------- #
# Loading a run
# --------------------------------------------------------------------------- #


@dataclass
class RunData:
    results_dir: Path
    blocks: list[Block]
    tables: dict[str, Table]
    hashes: dict[str, str]
    problems: list[str]
    element_order: list[str]
    warnings: list[str] = dataclass_field(default_factory=list)
    calcfold: dict[int, dict[str, object]] = dataclass_field(default_factory=dict)

    def fields_for(self, block: Block, generations: set[int] | None = None) -> dict[str, object]:
        values: dict[str, object] = {
            "id": block.id,
            "natoms": block.natoms,
            "nelements": block.nelements,
            "formula": block.formula,
            "element": [element for element, count in zip(block.elements, block.counts) if count > 0],
            "spacegroup": block.spacegroup,
            "volume": block.volume,
            "source": block.source,
            "boundary": block.boundary,
        }
        if block.lattice_params:
            a, b, c, alpha, beta, gamma = block.lattice_params
            values.update({"a": a, "b": b, "c": c, "alpha": alpha, "beta": beta, "gamma": gamma})
        if block.lattice:
            values["a"] = _vector_length(block.lattice[0])
            values["b"] = _vector_length(block.lattice[1])
            values["c"] = _vector_length(block.lattice[2])
            values["alpha"] = _angle(block.lattice[1], block.lattice[2])
            values["beta"] = _angle(block.lattice[0], block.lattice[2])
            values["gamma"] = _angle(block.lattice[0], block.lattice[1])

        pareto = self.tables.get("pareto")
        if pareto is not None:
            row = pareto.by_id().get(block.id)
            if row is not None:
                values["front"] = _int(row.get("Pareto"))
                values["enthalpy_atom"] = _float(row.get("Enthalpy"))
                values["hull"] = _float(row.get("ConvexHull") or row.get("Convex_hull"))
                values["origin"] = row.get("Origin")
                objective = _objective_column(pareto, exclude=("ConvexHull", "Convex_hull"))
                values["objective"] = _float(row.get(objective)) if objective else None

        individuals = self.tables.get("individuals")
        if individuals is not None:
            rows = individuals.rows_by_id().get(block.id, [])
            if generations is not None:
                rows = [row for row in rows if _int(row.get("Gen")) in generations]
            # One row only: anything else is ambiguous, and a wrong pick would be
            # a silent misattribution, so the field simply stays unset.
            if len(rows) == 1:
                row = rows[0]
                values["generation"] = _int(row.get("Gen"))
                values["enthalpy_cell"] = _float(row.get("Enthalpy"))
                values["density"] = _float(row.get("Density"))
                objective = _objective_column(individuals)
                values["objective_ind"] = _float(row.get(objective)) if objective else None

        hull = self.tables.get("hull")
        if hull is not None:
            row = hull.by_id().get(block.id)
            if row is not None:
                values["hull_block"] = _float(row.get("Fitness"))
                values["enthalpy_atom_hull"] = _float(row.get("Enthalpies"))
                values["y_axis"] = _float(row.get("Y"))

        return values


def _float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value))
    except ValueError:
        return None


def _int(value: object) -> int | None:
    number = _float(value)
    return int(number) if number is not None else None


def _objective_column(table: Table, exclude: tuple[str, ...] = ()) -> str | None:
    known = {
        "Gen", "ID", "Origin", "Composition", "Enthalpy", "Volume", "Density",
        "KPOINTS", "SYMM", "Q_entr", "A_order", "S_order", "Pareto", "ConvexHull",
        "Convex_hull", "front", "Fitness", "Enthalpies", "Volumes", "Compositions", "X", "Y",
    } | set(exclude)
    for column in table.columns:
        if column in known or column.startswith("("):
            continue
        return column
    return None


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_stable(path: Path) -> tuple[str, bool]:
    """Read a file and report whether it changed underneath us.

    ``gatheredPOSCARS`` is appended to while a run is alive, so a selection made
    mid-write can silently miss the newest structures.
    """
    before = path.stat()
    text = path.read_text(encoding="utf-8", errors="replace")
    after = path.stat()
    changed = before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns
    return text, changed


def load_run(
    results_dir: Path,
    *,
    sources: tuple[str, ...] = ("relaxed",),
    strict: bool = True,
    needs_calcfold: bool = False,
) -> RunData:
    problems: list[str] = []
    warnings: list[str] = []
    blocks: list[Block] = []
    hashes: dict[str, str] = {}
    for source in sources:
        path = results_dir / POSCAR_SOURCES[source]
        if not path.is_file():
            problems.append(f"missing {path.name}")
            continue
        hashes[path.name] = sha256(path)
        text, changed = read_stable(path)
        if changed:
            warnings.append(
                f"{path.name} changed while it was being read (the run is still writing it); "
                "the newest structures may be absent from this selection"
            )
        parsed, issues = parse_poscar_blocks(text, source, strict)
        blocks.extend(parsed)
        problems.extend(issues)

    tables: dict[str, Table] = {}
    for key, (filename, header_rows) in KNOWN_TABLE_SOURCES.items():
        path = results_dir / filename
        if not path.is_file():
            continue
        hashes[filename] = sha256(path)
        text, changed = read_stable(path)
        if changed:
            warnings.append(
                f"{filename} changed while it was being read (the run is still writing it); "
                "values in this selection may be from different generations"
            )
        tables[key] = parse_table(text, key, path, header_rows)

    element_order: list[str] = []
    for block in blocks:
        if block.elements:
            element_order = block.elements
            break

    run = RunData(
        results_dir=results_dir,
        blocks=blocks,
        tables=tables,
        hashes=hashes,
        problems=problems,
        element_order=element_order,
        warnings=warnings,
    )
    if needs_calcfold:
        run.calcfold = attach_calcfold(results_dir, blocks)
    return run


def attach_calcfold(results_dir: Path, blocks: list[Block]) -> dict[int, dict[str, object]]:
    """Map structure id -> convergence evidence from the current generation's CalcFold*."""
    mapping: dict[int, dict[str, object]] = {}
    history = results_dir.parent / "CalcFoldTemp" / "Jobs.history"
    fold_by_structure: dict[int, int] = {}
    if history.is_file():
        for line in history.read_text(encoding="utf-8", errors="replace").splitlines():
            match = re.search(r"Structure\s+(\d+)\s+at\s+CalcFold\s*(\d+)", line, re.IGNORECASE)
            if match:
                fold_by_structure[int(match.group(1))] = int(match.group(2))
    for block in blocks:
        fold = fold_by_structure.get(block.id)
        if fold is None:
            continue
        energy_path = results_dir.parent / f"CalcFold{fold}" / "energy.txt"
        if not energy_path.is_file():
            mapping[block.id] = {"converged": None, "energy_cell": None}
            continue
        text = energy_path.read_text(encoding="utf-8", errors="replace").strip()
        try:
            value = float(text.splitlines()[0].strip())
        except (ValueError, IndexError):
            mapping[block.id] = {"converged": None, "energy_cell": None}
            continue
        mapping[block.id] = {"converged": value < SENTINEL_ENERGY, "energy_cell": value}
    return mapping


# --------------------------------------------------------------------------- #
# Selection
# --------------------------------------------------------------------------- #


@dataclass
class Selection:
    matched: list[tuple[Block, dict[str, object]]]
    counts: dict[str, int]
    notes: list[str]


def requested_generations(predicates: list[Node]) -> set[int] | None:
    """Generations constrained by the conditions themselves, or None if unconstrained."""
    allowed: set[int] = set()
    constrained = False

    def walk(node: Node) -> None:
        nonlocal constrained
        if node.field == "generation":
            constrained = True
            if node.op == "range":
                low = _as_float(node.values[0]) if node.values[0] else None
                high = _as_float(node.values[-1]) if node.values[-1] else None
                if low is not None and high is not None and high - low <= 64:
                    allowed.update(range(int(low), int(high) + 1))
            else:
                for value in node.values:
                    number = _as_float(value)
                    if number is not None:
                        allowed.add(int(number))
        for child in node.children:
            walk(child)

    for predicate in predicates:
        walk(predicate)
    return allowed if constrained else None


def select(run: RunData, predicates: list[Node], limit: int | None, sort_key: str | None, descending: bool) -> Selection:
    calcfold: dict[int, dict[str, object]] = getattr(run, "calcfold", {})
    generations = requested_generations(predicates)
    counts = {"considered": 0, "matched": 0, "missing_field": 0, "not_matched": 0, "duplicate_id_in_source": 0}
    matched: list[tuple[Block, dict[str, object]]] = []
    notes: list[str] = []
    seen_keys: set[tuple[str, int]] = set()
    for block in run.blocks:
        counts["considered"] += 1
        key = (block.source, block.id)
        if key in seen_keys:
            counts["duplicate_id_in_source"] += 1
            continue
        seen_keys.add(key)
        values = run.fields_for(block, generations)
        values.update(calcfold.get(block.id, {}))
        decision: bool | None = True
        for predicate in predicates:
            outcome = evaluate(predicate, values)
            if outcome is False:
                decision = False
                break
            if outcome is None:
                decision = None
        if decision is False:
            counts["not_matched"] += 1
            continue
        if decision is None:
            counts["missing_field"] += 1
            continue
        matched.append((block, values))

    if sort_key:
        spec = FIELD_BY_NAME.get(sort_key)
        if spec is None:
            raise PredicateError(f"unknown sort field {sort_key!r}")
        if spec.kind not in NUMERIC_KINDS:
            matched.sort(key=lambda item: str(item[1].get(sort_key) or ""), reverse=descending)
        else:
            matched.sort(
                key=lambda item: (item[1].get(sort_key) is None, _as_float(item[1].get(sort_key)) or 0.0),
                reverse=descending,
            )
    if limit is not None:
        matched = matched[:limit]
    counts["matched"] = len(matched)
    if counts["missing_field"]:
        notes.append(
            f"{counts['missing_field']} structure(s) were excluded because a field referenced by the "
            "condition has no value for them; missing evidence is never treated as zero"
        )
    return Selection(matched=matched, counts=counts, notes=notes)


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #


def render_name(template: str, block: Block, values: dict[str, object]) -> str:
    fields = {
        "id": block.id,
        "EA": f"EA{block.id}",
        "formula": block.formula,
        "natoms": block.natoms,
        "nelements": block.nelements,
        "symm": block.spacegroup if block.spacegroup is not None else "NA",
        "source": block.source,
        "volume": f"{block.volume:.3f}" if block.volume else "NA",
    }
    try:
        name = template.format(**fields)
    except KeyError as exc:
        raise PredicateError(f"unknown name template field {exc.args[0]!r}") from exc
    return re.sub(r"[^A-Za-z0-9._+-]+", "_", name)


def write_outputs(
    run: RunData,
    selection: Selection,
    predicates: list[Node],
    out_dir: Path,
    *,
    template: str,
    archive: Path | None,
    include_values: bool,
) -> dict[str, object]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[dict[str, object]] = []
    used_names: dict[str, int] = {}
    for block, values in selection.matched:
        name = render_name(template, block, values)
        if name in used_names:
            # Two sources can legitimately carry the same id; never overwrite.
            used_names[name] += 1
            name = f"{name}_{block.source}"
            if name in used_names:
                name = f"{name}_{used_names[name]}"
        used_names.setdefault(name, 0)
        name = name + ".vasp"
        destination = out_dir / name
        destination.write_text("\n".join(block.lines) + "\n", encoding="utf-8")
        record: dict[str, object] = {
            "file": name,
            "id": block.id,
            "formula": block.formula,
            "natoms": block.natoms,
            "spacegroup": block.spacegroup,
            "source": block.source,
        }
        if include_values:
            record["evidence"] = {
                key: values.get(key)
                for key in sorted({name for predicate in predicates for name in node_fields(predicate)})
            }
        written.append(record)

    csv_path = out_dir / "candidates.csv"
    columns = ["file", "id", "formula", "natoms", "nelements", "spacegroup", "volume", "source", "boundary"]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        for record in written:
            writer.writerow([record.get(column, "") for column in columns])

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "results_dir": str(run.results_dir),
        "conditions": [render_predicate(predicate) for predicate in predicates],
        "fields_used": {
            name: {"source": FIELD_BY_NAME[name].source, "unit": FIELD_BY_NAME[name].unit}
            for name in sorted({name for predicate in predicates for name in node_fields(predicate)})
        },
        "counts": selection.counts,
        "notes": selection.notes,
        "warnings": run.warnings,
        "source_files": run.hashes,
        "structures": written,
        "excluded_from_bundle": ["results*/generation*", "CalcFold*", "*.mat", "*.log"],
    }
    manifest_path = out_dir / "selection_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    result: dict[str, object] = {
        "output_dir": str(out_dir),
        "structures": len(written),
        "csv": str(csv_path),
        "manifest": str(manifest_path),
        "counts": selection.counts,
    }
    if archive is not None:
        archive.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive, "w:gz") as tar:
            for record in written:
                tar.add(out_dir / str(record["file"]), arcname=str(record["file"]))
            tar.add(csv_path, arcname=csv_path.name)
            tar.add(manifest_path, arcname=manifest_path.name)
        result["archive"] = str(archive)
        result["archive_bytes"] = archive.stat().st_size
    return result


def describe_fields(run: RunData) -> dict[str, object]:
    available: dict[str, object] = {}
    sample = run.blocks[0] if run.blocks else None
    values = run.fields_for(sample) if sample else {}
    for spec in FIELD_SPECS:
        present = spec.name in values and values.get(spec.name) is not None
        available[spec.name] = {
            "kind": spec.kind,
            "source": spec.source,
            "unit": spec.unit,
            "available_in_this_run": present,
            "help": spec.help,
        }
    return available


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  extract_uspex_candidates.py results1 --list-fields\n"
            "  extract_uspex_candidates.py results1 --select 'nelements<=2' --select 'natoms=4..20' --dry-run\n"
            "  extract_uspex_candidates.py results1 --select 'front=1' --select 'hull<=0.05' --out picked/\n"
            "  extract_uspex_candidates.py results1 --select 'element in Li,Mg' --select 'spacegroup>=190' \\\n"
            "      --out picked/ --archive picked.tar.gz\n"
            "exit codes: 0 wrote structures, 3 nothing matched, 2 invalid request or unusable data"
        ),
    )
    parser.add_argument("results_dir", type=Path, help="a results<N> directory (or the run root)")
    parser.add_argument("--select", action="append", default=[], help="condition; repeat for AND")
    parser.add_argument("--out", type=Path, help="output directory for the packaged structures")
    parser.add_argument("--archive", type=Path, help="also write these structures to a tar.gz")
    parser.add_argument("--list-fields", action="store_true", help="print the condition vocabulary and exit")
    parser.add_argument("--dry-run", action="store_true", help="select and report, write nothing")
    parser.add_argument("--source", choices=("relaxed", "unrelaxed", "both"), default="relaxed")
    parser.add_argument("--include-unrelaxed", action="store_true", help="shorthand for --source both")
    parser.add_argument("--name-template", default="EA{id}_{formula}_SG{symm}")
    parser.add_argument("--sort", dest="sort_key", help="field to sort by")
    parser.add_argument("--descending", action="store_true")
    parser.add_argument("--limit", type=int, help="keep only the first N after sorting")
    parser.add_argument("--no-evidence", action="store_true", help="omit per-structure field values from the manifest")
    parser.add_argument("--skip-invalid", action="store_true", help="continue past malformed blocks and report them")
    parser.add_argument("--json", action="store_true", help="emit a machine-readable summary")
    args = parser.parse_args(argv)

    sources: tuple[str, ...]
    if args.include_unrelaxed or args.source == "both":
        sources = ("relaxed", "unrelaxed")
    else:
        sources = (args.source,)

    results_dir = args.results_dir
    if results_dir.name != "results1" and not results_dir.name.startswith("results"):
        candidate = results_dir / "results1"
        if candidate.is_dir():
            results_dir = candidate
    if not results_dir.is_dir():
        print(f"FAIL: not a directory: {results_dir}", file=sys.stderr)
        return EXIT_USAGE

    try:
        predicates = [parse_predicate(text) for text in args.select]
    except PredicateError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return EXIT_USAGE

    if not predicates and not args.list_fields:
        print("FAIL: nothing to select; pass --select or --list-fields", file=sys.stderr)
        return EXIT_USAGE

    needs_calcfold = any("calcfold" in node_sources(predicate) for predicate in predicates)
    try:
        run = load_run(results_dir, sources=sources, strict=not args.skip_invalid, needs_calcfold=needs_calcfold)
    except (ParseFailure, OSError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return EXIT_USAGE

    if args.list_fields:
        payload = {
            "results_dir": str(results_dir),
            "fields": describe_fields(run),
            "problems": run.problems,
            "warnings": run.warnings,
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return EXIT_OK

    # Individuals ids repeat per generation; refuse an ambiguous join.
    if any("individuals" in node_sources(predicate) for predicate in predicates):
        if not any("generation" in node_fields(predicate) for predicate in predicates):
            print(
                "FAIL: a condition uses Individuals-derived fields without 'generation='; "
                "Individuals ids repeat across generations, so the join would be ambiguous",
                file=sys.stderr,
            )
            return EXIT_USAGE

    if args.limit is not None and args.limit <= 0:
        print("FAIL: --limit must be positive", file=sys.stderr)
        return EXIT_USAGE

    try:
        selection = select(run, predicates, args.limit, args.sort_key, args.descending)
    except PredicateError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return EXIT_USAGE

    if args.dry_run or args.out is None:
        payload = {
            "results_dir": str(results_dir),
            "conditions": [render_predicate(predicate) for predicate in predicates],
            "counts": selection.counts,
            "notes": selection.notes + run.problems,
            "warnings": run.warnings,
            "matched_ids": [block.id for block, _ in selection.matched[:50]],
            "dry_run": args.dry_run,
        }
        if args.json:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        if args.out is None and not args.dry_run:
            print("note: no --out given, so nothing was written", file=sys.stderr)
        return EXIT_OK if selection.counts["matched"] else EXIT_EMPTY

    try:
        result = write_outputs(
            run,
            selection,
            predicates,
            args.out,
            template=args.name_template,
            archive=args.archive,
            include_values=not args.no_evidence,
        )
    except (OSError, PredicateError, tarfile.TarError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return EXIT_USAGE
    result["problems"] = run.problems
    result["notes"] = selection.notes
    result["warnings"] = run.warnings
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return EXIT_OK if result["structures"] else EXIT_EMPTY


if __name__ == "__main__":
    raise SystemExit(main())
