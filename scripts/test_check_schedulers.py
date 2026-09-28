#!/usr/bin/env python3
"""Offline tests for check_schedulers.py; no scheduler installation required.

Every test feeds recorded-style scheduler output through the parsers, so the suite
never contacts a cluster and never submits anything.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import check_schedulers as cs


class FakeProbe:
    """A Probe whose command results come from a lookup table."""

    def __init__(self, table: dict[tuple[str, ...], tuple[int, str, str]]) -> None:
        self.table = table
        self.calls: list[tuple[str, ...]] = []

    def run(self, *argv: str) -> cs.CommandResult:
        self.calls.append(tuple(argv))
        rc, out, err = self.table.get(tuple(argv), (127, "", "command not found"))
        return cs.CommandResult(list(argv), rc, out, err)


SINFO = """\
gpu*|up|1-00:00:00|2|32|256000|gpu:a100:4|(null)
cpu|up|7-00:00:00|20|64|512000|(null)|(null)
debug|down|00:30:00|1|8|16000|(null)|(null)
"""

SINFO_N = """\
gpu|up|idle|2|node[01-02]
cpu|up|mix|20|node[03-22]
debug|down|down*|1|node23
"""

SCONTROL = """\
PartitionName=gpu
   AllowGroups=ALL AllowAccounts=proj123,proj456
   MaxTime=1-00:00:00 MaxNodes=4 TRES=cpu=32,mem=256000M,node=2
   State=UP TotalCPUs=64 TotalNodes=2

PartitionName=cpu
   AllowGroups=physics
   MaxTime=7-00:00:00 MaxNodes=100
   State=UP

PartitionName=debug
   MaxTime=00:30:00
   State=DRAIN

"""

QSTAT_Q = """\
Queue                     Memory CPU Time Walltime Node  Run Que Lm  State
---------------------- -------- -------- -------- ---- --- --- -- -----
workq                    --       --     336:00:00  --   12  3  20   E R
gpu                        --       --     24:00:00   --    4  0   8   E R
debug                      --       --      0:30:00   --    0  0   2   D
"""

QSTAT_PER_QUEUE = """\
server: pbs.example

Queue: workq
    queue_type = Execution
    resources_max.walltime = 336:00:00
    resources_default.walltime = 24:00:00
    enabled = True
    started = True
    acl_group = users

Queue: gpu
    queue_type = Execution
    resources_max.walltime = 24:00:00
    enabled = True
    started = True

Queue: scratch
    queue_type = Execution
    enabled = False
    started = True
"""

PBSNODES = """\
node01
    state = free
    queue = workq
    np = 32

node02
    state = down
    queue = gpu
    np = 16
"""

# Captured from pbs-lab: node hostnames carry a domain suffix and most nodes
# declare no "queue =" (they are unassigned), which must not hide the rest.
PBSNODES_DOTTED = """\
node01.cluster
     state = free
     np = 28
     ntype = cluster
     jobs = 16/1000001.node01,17/1000001.node01

workq.node1
     state = free
     np = 32
     queue = workq
"""

# Captured verbatim from pbs-lab (CentOS 7, PBS-derived GridView dispatcher).
REAL_GVIEW_Q = """\
Queue              Max    Tot   Ena   Str   Que   Run   Hld   Wat   Trn   Ext T   Cpt
----------------   ---   ----    --    --   ---   ---   ---   ---   ---   --- -   ---
intel_high           0     10   yes   yes     5     5     0     0     0     0 E     0
USPEX                0      0   yes   yes     0     0     0     0     0     0 E     0
amd                  0      0    no    no     0     0     0     0     0     0 E     0
intel_middle         0    149   yes   yes   129    19     0     0     0     0 E     1
debug                0      0    no    no     0     0     0     0     0     0 E     0
"""

REAL_GVIEW_PER_QUEUE = """\
server: node1

Queue            Memory CPU Time Walltime Node  Run Que Lm  State
---------------- ------ -------- -------- ----  --- --- --  -----
intel_high         --      --       --      --    5   5  0   E R
USPEX              --      --       --      --    0   0  0   E R
amd                --      --       --      --    0   0  0   D S
intel_middle       --      --       --      --   19 129  0   E R
debug              --      --       --      --    0   0  0   D S
                                               ----- -----
                                                  24   134
"""

# Captured verbatim from pbs-lab: the GridView dispatcher wraps long values, and
# one wrap splits the word "Complete" across lines. acl_users is where access lives.
REAL_QSTAT_QF = """\
Queue: USPEX
    queue_type = Execution
    total_jobs = 0
    state_count = Transit:0 Queued:0 Held:0 Waiting:0 Running:0 Exiting:0 Comp
        lete:0
    max_running = 0
    acl_host_enable = False
    acl_hosts = node01,node02,node03,node04,node05
    acl_user_enable = True
    acl_users = svc-metrics@*,daemon@*,alice@*,
        bob@*,carol@*,root@*
    resources_default.walltime = 100:00:00
    mtime = 1787220675
    max_user_run = 0
    enabled = True
    started = True
"""

# Captured verbatim from 4090 (Slurm 22.05.9): %l prints the literal "infinite".
REAL_SLURM_SINFO = """\
uspex|up|infinite|1|192|505000|gpu:4|(null)
high*|up|infinite|1|192|505000|gpu:4|(null)
gpu|up|infinite|1|192|505000|gpu:4|(null)
"""


class SlurmParsingTests(unittest.TestCase):
    def test_sinfo_partitions_carry_state_and_limits(self) -> None:
        queues = {queue.name: queue for queue in cs._parse_sinfo_partitions(SINFO)}
        self.assertEqual(set(queues), {"gpu", "cpu", "debug"})
        self.assertTrue(queues["gpu"].available)
        self.assertFalse(queues["debug"].available)
        self.assertIn("walltime=1-00:00:00", queues["gpu"].limits)
        self.assertIn("gres=gpu:a100:4", queues["gpu"].limits)

    def test_starred_default_partition_is_normalised(self) -> None:
        queues = cs._parse_sinfo_partitions("gpu*|up|1:00:00|1|8|16000|(null)|(null)\n")
        self.assertEqual(queues[0].name, "gpu")

    def test_real_slurm_infinite_walltime_is_normalised(self) -> None:
        queues = {queue.name: queue for queue in cs._parse_sinfo_partitions(REAL_SLURM_SINFO)}
        self.assertEqual(set(queues), {"uspex", "high", "gpu"})
        self.assertIn("walltime=UNLIMITED", queues["uspex"].limits)
        self.assertNotIn("infinite", queues["uspex"].limits)
        self.assertIn("gres=gpu:4", queues["gpu"].limits)

    def test_down_nodes_make_a_partition_unavailable(self) -> None:
        queues = {queue.name: queue for queue in cs._parse_sinfo_long(SINFO_N)}
        self.assertFalse(queues["debug"].available)
        self.assertTrue(queues["cpu"].available)

    def test_merge_keeps_the_worst_availability(self) -> None:
        up = cs.Queue(name="gpu", state="up", available=True, source="sinfo")
        down = cs.Queue(name="gpu", state="down", available=False, reason="node down", source="sinfo -N")
        merged = cs._merge_queues([up], [down])
        self.assertEqual(len(merged), 1)
        self.assertFalse(merged[0].available)
        self.assertIn("node down", merged[0].reason)

    def test_scontrol_parsing_adds_limits_and_drain_state(self) -> None:
        blocks = cs._parse_scontrol_partition(SCONTROL)
        self.assertEqual(blocks["gpu"]["AllowAccounts"], "proj123,proj456")
        self.assertEqual(blocks["debug"]["State"], "DRAIN")

    def test_slurm_probe_marks_drained_partition_and_notes_acl(self) -> None:
        table = {
            ("sinfo", "--version"): (0, "slurm 23.11.4\n", ""),
            ("sinfo", "-h", "-o", "%P|%a|%l|%D|%c|%m|%G|%f"): (0, SINFO, ""),
            ("sinfo", "-h", "-N", "-o", "%P|%a|%t|%D|%N"): (0, SINFO_N, ""),
            ("scontrol", "show", "partition"): (0, SCONTROL, ""),
        }
        commands = {name: f"/usr/bin/{name}" for name in ("sbatch", "sinfo", "squeue", "scontrol")}
        scheduler = cs.probe_slurm(commands, FakeProbe(table))
        queues = {queue.name: queue for queue in scheduler.queues}
        self.assertEqual(scheduler.version, "slurm 23.11.4")
        self.assertFalse(queues["debug"].available)
        self.assertIn("scontrol State=DRAIN", queues["debug"].reason)
        self.assertTrue(any("AllowGroups=physics" in note for note in scheduler.notes))

    def test_missing_client_is_reported_not_crashed(self) -> None:
        scheduler = cs.probe_slurm({}, FakeProbe({}))
        self.assertTrue(any("sbatch is not on PATH" in error for error in scheduler.errors))


class PbsParsingTests(unittest.TestCase):
    def test_qstat_Q_default_partition_layout(self) -> None:
        queues = {queue.name: queue for queue in cs._parse_qstat_queues(QSTAT_Q)}
        self.assertEqual(set(queues), {"workq", "gpu", "debug"})
        self.assertTrue(queues["workq"].available)
        self.assertFalse(queues["debug"].available)
        # The header column is literally named "Time" in this qstat layout.
        self.assertIn("Time=336:00:00", queues["workq"].limits)
        self.assertIn("Que=20", queues["workq"].limits)
        # An "E" in the Node/Lm columns means enabled, not a disabled queue.
        self.assertIn("Lm=E", queues["gpu"].limits)
        # PBS "E" means enabled (healthy), so it must never disable a queue.
        self.assertEqual(queues["gpu"].state, "R")

    def test_qstat_Q_alternative_column_layout(self) -> None:
        text = (
            "Queue          Max    Tot   Ena   Str   Que   Run   Hld   Wat   Trn   Ext State\n"
            "workq           20     15   yes   yes     3    12     0     0     0     0   R\n"
            "gpu              8      4   yes   yes     0     4     0     0     0     0   R\n"
        )
        queues = {queue.name: queue for queue in cs._parse_qstat_queues(text)}
        self.assertEqual(set(queues), {"workq", "gpu"})
        self.assertTrue(queues["gpu"].available)

    def test_per_queue_attributes_disable_a_queue(self) -> None:
        blocks = cs._parse_qstat_q(QSTAT_PER_QUEUE)
        self.assertEqual(set(blocks), {"workq", "gpu", "scratch"})
        self.assertEqual(blocks["gpu"]["resources_max.walltime"], "24:00:00")
        self.assertEqual(blocks["workq"]["acl_group"], "users")
        self.assertEqual(blocks["scratch"]["enabled"], "False")
        self.assertFalse([key for key in blocks if key.startswith("queue_")])

    def test_pbs_probe_disables_queue_and_flags_acl(self) -> None:
        table = {
            ("qstat", "--version"): (0, "PBS Pro 2021.1.3\n", ""),
            ("qstat", "-Q"): (0, QSTAT_Q, ""),
            ("qstat", "-q"): (0, QSTAT_PER_QUEUE, ""),
            ("pbsnodes", "-a"): (0, PBSNODES, ""),
        }
        commands = {name: f"/opt/pbs/bin/{name}" for name in ("qsub", "qstat", "qdel", "pbsnodes")}
        scheduler = cs.probe_pbs(commands, FakeProbe(table))
        queues = {queue.name: queue for queue in scheduler.queues}
        self.assertEqual(scheduler.family, "pbspro")
        self.assertFalse(queues["scratch"].available)
        self.assertFalse(queues["gpu"].available)
        self.assertIn("state=down", queues["gpu"].reason)
        self.assertIn("resources_max.walltime=336:00:00", queues["workq"].limits)
        self.assertTrue(any("acl=" in note for note in scheduler.notes))

    def test_admin_torque_layout_is_detected(self) -> None:
        text = (
            "Queue                     Max   Tot   Ena   Str   Que   Run   Hld   Wat   Trn   Ext State\n"
            "workq                     100    20   yes   yes    10    10     0     0     0     0   R\n"
        )
        queues = cs._parse_qstat_queues(text)
        self.assertEqual(queues[0].name, "workq")
        self.assertTrue(queues[0].available)

    def test_legacy_torque_probe_reads_D_column_as_disabled(self) -> None:
        table = {
            ("qstat", "--version"): (0, "version: 6.1.2 (Torque)\n", ""),
            ("qstat", "-Q"): (0, QSTAT_Q, ""),
            ("qstat", "-q"): (0, QSTAT_PER_QUEUE, ""),
        }
        commands = {"qsub": "/x/qsub", "qstat": "/x/qstat"}
        scheduler = cs.probe_pbs(commands, FakeProbe(table))
        queues = {queue.name: queue for queue in scheduler.queues}
        self.assertFalse(queues["debug"].available)
        self.assertTrue(queues["workq"].available)

    def test_real_gview_probe_marks_only_disabled_queues_unavailable(self) -> None:
        """Full probe_pbs run over the bytes captured from pbs-lab."""
        table = {
            ("qstat", "--version"): (1, "", "qstat: unknown option\n"),
            ("qstat", "-B"): (0, "Server  Max Tot Que Run Hld Wat Trn Ext Com Status\n"
                                 "node1  0 154 130 24 0 0 0 0 0 Active\n", ""),
            ("qstat", "-Q"): (0, REAL_GVIEW_Q, ""),
            ("qstat", "-q"): (0, REAL_GVIEW_PER_QUEUE, ""),
        }
        commands = {
            "qsub": "/opt/gridview/pbs/dispatcher/bin/qsub",
            "qstat": "/opt/gridview/pbs/dispatcher/bin/qstat",
        }
        scheduler = cs.probe_pbs(commands, FakeProbe(table))
        queues = {queue.name: queue for queue in scheduler.queues}
        self.assertEqual(
            set(queues), {"intel_high", "USPEX", "amd", "intel_middle", "debug"}
        )
        self.assertEqual(
            [name for name, queue in sorted(queues.items()) if queue.available],
            ["USPEX", "intel_high", "intel_middle"],
        )
        self.assertIn("Ena=no", queues["amd"].reason)
        self.assertIn("Str=no", queues["debug"].reason)
        # No pbsnodes on this cluster: say so instead of implying health was checked.
        self.assertTrue(
            any("pbsnodes" in note for note in scheduler.notes),
            msg=f"expected a pbsnodes note, got {scheduler.notes}",
        )

    def test_wrapped_acl_users_resolve_the_account(self) -> None:
        values = cs._parse_qstat_q(REAL_QSTAT_QF)["USPEX"]
        # The mid-word wrap must be rejoined, not left as "Comp" / "lete:0".
        self.assertIn("state_count", values)
        self.assertEqual(cs._acl_allows_user(values, "alice"), True)
        self.assertEqual(cs._acl_allows_user(values, "bob"), True)
        self.assertEqual(cs._acl_allows_user(values, "stranger"), False)
        self.assertEqual(values["resources_default.walltime"], "100:00:00")

    def test_acl_user_enable_false_admits_everyone(self) -> None:
        self.assertTrue(cs._acl_allows_user({"acl_user_enable": "False", "acl_users": "a@*"}, "x"))
        self.assertIsNone(cs._acl_allows_user({}, "x"))

    def test_gview_probe_denies_an_account_absent_from_acl_users(self) -> None:
        """End-to-end: an account outside acl_users is reported as NOT available."""
        table = {
            ("qstat", "--version"): (1, "", "qstat: unknown option\n"),
            ("qstat", "-B"): (0, "Server  Max Tot Que Run Hld Wat Trn Ext Com Status\n", ""),
            ("qstat", "-Q"): (0, REAL_GVIEW_Q, ""),
            ("qstat", "-q"): (0, REAL_GVIEW_PER_QUEUE, ""),
            ("qstat", "-Qf", "intel_high"): (0, REAL_QSTAT_QF.replace("USPEX", "intel_high"), ""),
            ("qstat", "-Qf", "USPEX"): (0, REAL_QSTAT_QF, ""),
            ("qstat", "-Qf", "intel_middle"): (0, REAL_QSTAT_QF.replace("USPEX", "intel_middle"), ""),
        }
        commands = {"qsub": "/x/qsub", "qstat": "/x/qstat"}
        with patch.object(cs.os.environ, "get", lambda name, default="": "stranger" if name == "USER" else default):
            scheduler = cs.probe_pbs(commands, FakeProbe(table))
        queues = {queue.name: queue for queue in scheduler.queues}
        self.assertFalse(queues["USPEX"].available)
        self.assertIn("acl_users does not list stranger", queues["USPEX"].reason)

    def test_gview_probe_admits_the_listed_account(self) -> None:
        table = {
            ("qstat", "--version"): (1, "", "qstat: unknown option\n"),
            ("qstat", "-B"): (0, "Server  Max Tot Que Run Hld Wat Trn Ext Com Status\n", ""),
            ("qstat", "-Q"): (0, REAL_GVIEW_Q, ""),
            ("qstat", "-q"): (0, REAL_GVIEW_PER_QUEUE, ""),
            ("qstat", "-Qf", "USPEX"): (0, REAL_QSTAT_QF, ""),
        }
        commands = {"qsub": "/x/qsub", "qstat": "/x/qstat"}
        with patch.object(cs.os.environ, "get", lambda name, default="": "alice" if name == "USER" else default):
            scheduler = cs.probe_pbs(commands, FakeProbe(table))
        queues = {queue.name: queue for queue in scheduler.queues}
        self.assertTrue(queues["USPEX"].available)
        self.assertTrue(any("admits alice" in note for note in scheduler.notes))
        self.assertIn("default_walltime=100:00:00", queues["USPEX"].limits)

    def test_dotted_node_names_and_unassigned_nodes(self) -> None:
        """Real pbsnodes output uses node names like node01.cluster."""
        states, source, counts, seen = cs._pbsnodos_availability(
            FakeProbe({("pbsnodes", "-a"): (0, PBSNODES_DOTTED, "")})
        )
        self.assertEqual(source, "pbsnodes")
        self.assertEqual(states.get("workq"), "free")
        self.assertIn("(unassigned)", states)
        self.assertEqual(counts.get("free"), 2)
        self.assertEqual(seen, 2)

    def test_qstat_failure_is_reported(self) -> None:
        table = {("qstat", "-Q"): (1, "", "qstat: cannot connect to server\n")}
        scheduler = cs.probe_pbs({"qsub": "/x/qsub", "qstat": "/x/qstat"}, FakeProbe(table))
        self.assertTrue(any("qstat -Q failed" in error for error in scheduler.errors))

    def test_real_gview_layout_disables_queues_via_ena_str(self) -> None:
        """The PBS-derived GridView layout has no State column: Ena/Str decide."""
        queues = {queue.name: queue for queue in cs._parse_qstat_queues(REAL_GVIEW_Q)}
        self.assertEqual(
            set(queues), {"intel_high", "USPEX", "amd", "intel_middle", "debug"}
        )
        self.assertTrue(queues["intel_high"].available)
        self.assertTrue(queues["USPEX"].available)
        self.assertFalse(queues["amd"].available)
        self.assertFalse(queues["debug"].available)
        self.assertIn("Ena=no", queues["amd"].reason)
        self.assertIn("Str=no", queues["amd"].reason)
        # The trailing "T" column is site-specific; never report a job count as state.
        self.assertNotIn(queues["amd"].state, {"0", "1"})

    def test_real_gview_layout_reports_busy_queues(self) -> None:
        queues = {queue.name: queue for queue in cs._parse_qstat_queues(REAL_GVIEW_Q)}
        self.assertIn("Que=129", queues["intel_middle"].limits)
        self.assertIn("Run=19", queues["intel_middle"].limits)
        self.assertNotIn("Hld=0", queues["intel_middle"].limits)

    def test_real_gview_per_queue_is_diagnostic_only(self) -> None:
        """The GridView `qstat -q` block has no key=value pairs, so it yields nothing.

        The authoritative availability answer must then come from `qstat -Q` (Ena/Str).
        """
        blocks = cs._parse_qstat_q(REAL_GVIEW_PER_QUEUE)
        self.assertEqual(blocks, {})

    def test_gview_probe_keeps_qstat_Q_verdict(self) -> None:
        table = {
            ("qstat", "--version"): (0, "version: 6.1.2 (Torque)\n", ""),
            ("qstat", "-Q"): (0, REAL_GVIEW_Q, ""),
            ("qstat", "-q"): (0, REAL_GVIEW_PER_QUEUE, ""),
        }
        scheduler = cs.probe_pbs({"qsub": "/x/qsub", "qstat": "/x/qstat"}, FakeProbe(table))
        queues = {queue.name: queue for queue in scheduler.queues}
        self.assertEqual(len(queues), 5)
        self.assertFalse(queues["amd"].available)
        self.assertFalse(queues["debug"].available)
        self.assertTrue(queues["intel_middle"].available)
        self.assertNotIn(queues["amd"].state, {"0", "1"})

    def test_trailing_totals_rows_are_not_queues(self) -> None:
        queues = cs._parse_qstat_queues(REAL_GVIEW_Q + "                                               ----- -----\n                                                  24   134\n")
        self.assertNotIn("24", {queue.name for queue in queues})

    def test_missing_pbsnodes_is_reported_as_environmental(self) -> None:
        table = {
            ("qstat", "--version"): (0, "version: 6.1.2 (Torque)\n", ""),
            ("qstat", "-Q"): (0, REAL_GVIEW_Q, ""),
            ("qstat", "-q"): (0, REAL_GVIEW_PER_QUEUE, ""),
        }
        # pbsnodes deliberately absent from the command table -> "command not found".
        scheduler = cs.probe_pbs(
            {"qsub": "/x/qsub", "qstat": "/x/qstat"}, FakeProbe(table)
        )
        self.assertTrue(
            any(
                "pbsnodes" in note and ("not on PATH" in note or "no usable node list" in note)
                for note in scheduler.notes
            )
        )


class KindDetectionTests(unittest.TestCase):
    def test_sbatch_means_slurm(self) -> None:
        self.assertEqual(cs._detect_kind({"sbatch": "/usr/bin/sbatch"}), "slurm")

    def test_qsub_alone_is_ambiguous_and_reads_the_banner(self) -> None:
        probe = FakeProbe({("qstat", "--version"): (0, "PBS Pro 19.2\n", "")})
        self.assertEqual(cs._detect_kind({"qsub": "/x/qsub"}, probe), "pbspro")

    def test_torque_banner(self) -> None:
        probe = FakeProbe({("qstat", "--version"): (0, "version: 6.1.2 (Torque)\n", "")})
        self.assertEqual(cs._detect_kind({"qsub": "/x/qsub"}, probe), "torque")

    def test_unknown_without_any_client(self) -> None:
        self.assertEqual(cs._detect_kind({}), "unknown")


class SubmissionRequestTests(unittest.TestCase):
    def test_template_placeholders_are_reported_as_blockers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            submission = root / "Submission"
            submission.mkdir()
            (submission / "submitJob_local.py").write_text(
                'PARTITION = "<PARTITION>"\nWALLTIME = "06:00:00"\n', encoding="utf-8"
            )
            found, examined = cs._read_submission_request(root)
            self.assertEqual(examined, ["submitJob_local.py"])
            self.assertTrue(any("template placeholder" in item for item in found))

    def test_configured_queue_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            submission = root / "Submission"
            submission.mkdir()
            (submission / "submitJob_local.py").write_text(
                'QUEUE = "workq"\nRESOURCE_LINE = "nodes=1:ppn=8"\n', encoding="utf-8"
            )
            found, _ = cs._read_submission_request(root)
            self.assertTrue(any("PBS queue = workq" in item for item in found))
            self.assertTrue(any("PBS resource request = nodes=1:ppn=8" in item for item in found))

    def test_missing_submission_directory_is_not_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(cs._read_submission_request(Path(tmp)), ([], []))


class SubmissionDiscoveryTests(unittest.TestCase):
    def _run_dir(self, root: Path, which: str = "1") -> Path:
        run = root / "my-search"
        run.mkdir(parents=True)
        (run / "INPUT.txt").write_text(
            f"1 : whichCluster\n0 : AutoFrac\n", encoding="utf-8"
        )
        if which != "none":
            (run / "Submission").mkdir()
        return run

    def test_missing_submission_reports_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = root / "my-search"
            run.mkdir()
            (run / "INPUT.txt").write_text("1 : whichCluster\n", encoding="utf-8")
            state, which = cs._submission_state(run)
            self.assertEqual(state, "missing")
            self.assertEqual(which, 1)

    def test_template_pair_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = self._run_dir(Path(tmp))
            (run / "Submission" / "submitJob_local.py").write_text(
                'PARTITION = "<PARTITION>"\n', encoding="utf-8"
            )
            self.assertEqual(cs._submission_state(run)[0], "template")

    def test_existing_site_pair_is_offered_for_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / "old-project"
            (old / "Submission").mkdir(parents=True)
            (old / "Submission" / "submitJob_local.py").write_text(
                'QUEUE = "workq"\nRESOURCE_LINE = "nodes=1:ppn=8"\n', encoding="utf-8"
            )
            run = self._run_dir(root)
            advice = cs.submission_advice(run, [root])
            self.assertEqual(advice["verdict"], "reuse-existing")
            self.assertTrue(any(item["queue"] == "workq" for item in advice["candidates"]))
            self.assertIn("copy", str(advice["action"]))

    def test_only_templates_found_means_adapt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = self._run_dir(root)
            # Isolate the tree so no real site copy is discovered, and point the
            # skill root at a directory holding only neutral templates.
            fake_skill = root / "skill"
            assets = fake_skill / "assets" / "submission"
            assets.mkdir(parents=True)
            for name in ("submitJob_local.py", "checkStatus_local.py"):
                (assets / name).write_text(
                    'QUEUE = "<QUEUE>"\nJOB_PREAMBLE = """<LOAD_MODULES_AND_ACTIVATE_ENV>"""\n',
                    encoding="utf-8",
                )
            with patch.object(cs, "SKILL_ROOT", fake_skill), patch.object(
                cs, "_iter_submission_pairs", wraps=cs._iter_submission_pairs
            ):
                advice = cs.submission_advice(run, [root / "empty-root"])
            self.assertEqual(advice["verdict"], "adapt-template")

    def test_site_copy_without_a_queue_constant_is_still_reusable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / "old-project"
            (old / "Submission").mkdir(parents=True)
            (old / "Submission" / "submitJob_local.py").write_text(
                'QUEUE = "<QUEUE>"\n'
                'JOB_PREAMBLE = """<LOAD_MODULES_AND_ACTIVATE_ENV>"""\n'
                "def submit_job_local(index, command):\n"
                '    return command\n',
                encoding="utf-8",
            )
            (old / "Submission" / "checkStatus_local.py").write_text("x=1\n", encoding="utf-8")
            run = self._run_dir(root)
            old_cwd = os.getcwd()
            try:
                os.chdir(root)  # keep the skill's own repository out of the walk
                advice = cs.submission_advice(run, [root])
            finally:
                os.chdir(old_cwd)
            self.assertEqual(advice["verdict"], "reuse-existing")
            best = advice["candidates"][0]
            self.assertEqual(best["kind"], "site-copy")
            self.assertEqual(best["local_pair"], "yes")
            self.assertEqual(best["queue"], "")

    def test_script_copied_outside_the_skill_still_finds_site_copies(self) -> None:
        """A lone copy of the helper must not treat its own parent as the skill repo."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lone = root / "lone-script"
            lone.mkdir()
            old = root / "old-project"
            (old / "Submission").mkdir(parents=True)
            (old / "Submission" / "submitJob_local.py").write_text('QUEUE = "workq"\n', encoding="utf-8")
            (old / "Submission" / "checkStatus_local.py").write_text("x=1\n", encoding="utf-8")
            run = self._run_dir(root)
            with patch.object(cs, "SKILL_ROOT", lone):
                advice = cs.submission_advice(run, [root])
            self.assertEqual(advice["verdict"], "reuse-existing")
            self.assertTrue(any(item["queue"] == "workq" for item in advice["candidates"]))

    def test_incomplete_pair_is_completed_from_a_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / "old-project"
            (old / "Submission").mkdir(parents=True)
            (old / "Submission" / "submitJob_local.py").write_text('QUEUE = "workq"\n', encoding="utf-8")
            (old / "Submission" / "checkStatus_local.py").write_text("x=1\n", encoding="utf-8")
            run = self._run_dir(root)
            (run / "Submission" / "checkStatus_local.py").write_text("x=1\n", encoding="utf-8")
            advice = cs.submission_advice(run, [root])
            self.assertEqual(advice["state"], "pair_incomplete")
            self.assertEqual(advice["verdict"], "complete-from-copy")

    def test_nothing_found_means_ask_the_user(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = self._run_dir(Path(tmp))
            with patch.object(cs, "SKILL_ROOT", Path(tmp) / "no-skill-root"):
                advice = cs.submission_advice(run, [Path(tmp) / "empty-root"])
            self.assertEqual(advice["verdict"], "ask-user")
            action = str(advice["action"])
            self.assertIn("ask the user", action)
            self.assertIn("Do not invent", action)

    def test_configured_run_needs_no_advice(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = self._run_dir(Path(tmp))
            (run / "Submission" / "submitJob_local.py").write_text(
                'QUEUE = "workq"\n', encoding="utf-8"
            )
            (run / "Submission" / "checkStatus_local.py").write_text("x=1\n", encoding="utf-8")
            advice = cs.submission_advice(run, [Path(tmp)])
            self.assertEqual(advice["state"], "pair_present")
            self.assertEqual(advice["verdict"], "configured")

    def test_search_prunes_bulk_run_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            buried = root / "results1" / "CalcFold3" / "Submission"
            buried.mkdir(parents=True)
            (buried / "submitJob_local.py").write_text('QUEUE = "buried"\n', encoding="utf-8")
            found = list(cs._iter_submission_pairs([root]))
            self.assertEqual(found, [])


CLUSTER_GUIDE = """\
# Cluster Operating Guide

## Identity

- Cluster name: pbs-lab
- Login node policy: do not run jobs on node1

## Scheduler

- Scheduler: `pbs`
- Default queue/partition: `USPEX`
- Available queues/partitions:
  - intel_high:
    nodes/cores: 28-56
    walltime: 336:00:00
  - intel_middle:
  - USPEX:
- Submit command: `qsub submit.sh`
- Queue/status command: `qstat -u $USER`
- Cancel command: `qdel <id>`
- Accounting/history command: `tracejob <id>`
- Max unattended jobs: 20

## Storage

- Home path: /home/researcher
"""


class ClusterFactsTests(unittest.TestCase):
    def test_recorded_facts_are_parsed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".cluster-agents.md"
            path.write_text(CLUSTER_GUIDE, encoding="utf-8")
            facts = cs.read_cluster_facts(path)
            self.assertTrue(facts["exists"])
            self.assertEqual(facts["scheduler"], "pbs")
            self.assertEqual(facts["default_queue"], "USPEX")
            self.assertEqual(facts["submit_command"], "qsub submit.sh")
            self.assertEqual(facts["max_unattended_jobs"], "20")
            self.assertIn("intel_high", " ".join(facts["queues"]))
            self.assertEqual(len(str(facts["sha256"])), 64)

    def test_non_scheduler_sections_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".cluster-agents.md"
            path.write_text(CLUSTER_GUIDE, encoding="utf-8")
            facts = cs.read_cluster_facts(path)
            self.assertNotIn("Home path", str(facts.get("default_queue")))
            self.assertNotEqual(facts.get("scheduler"), "storage")

    def test_missing_guide_says_probe_then_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            advice = cs.facts_advice(cs.read_cluster_facts(Path(tmp) / "nope.md"))
            self.assertEqual(advice["verdict"], "no-guide")
            self.assertIn("offer to create", str(advice["action"]))

    def test_guide_without_a_queue_asks_the_user(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".cluster-agents.md"
            path.write_text("## Scheduler\n\n- Scheduler: `slurm`\n", encoding="utf-8")
            advice = cs.facts_advice(cs.read_cluster_facts(path))
            self.assertEqual(advice["verdict"], "guide-incomplete")
            self.assertIn("ask the user", str(advice["action"]))

    def test_recorded_queue_still_requires_verification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".cluster-agents.md"
            path.write_text(CLUSTER_GUIDE, encoding="utf-8")
            advice = cs.facts_advice(cs.read_cluster_facts(path))
            self.assertEqual(advice["verdict"], "recorded")
            self.assertEqual(advice["resolved_queue"], "USPEX")
            self.assertIn("confirm it on the live scheduler", str(advice["action"]))

    def test_rendered_block_never_invents_a_queue(self) -> None:
        block = cs.render_facts_block("slurm", [], "slurm 22.05.9", "alice")
        self.assertIn("- Scheduler: `slurm`", block)
        self.assertIn("- Default queue/partition:\n", block)
        self.assertIn("sbatch myrun", block)
        self.assertIn("alice", block)

    def test_rendered_block_proposes_only_available_queues(self) -> None:
        queues = [
            cs.Queue(name="uspex", state="up", available=True, limits="cpus=192 gres=gpu:4"),
            cs.Queue(name="debug", state="down", available=False, reason="scontrol State=DRAIN"),
        ]
        block = cs.render_facts_block("slurm", queues, "slurm 22.05.9", "alice")
        self.assertIn("- Default queue/partition: `uspex`", block)
        self.assertIn("debug: NOT available - scontrol State=DRAIN", block)


class MainTests(unittest.TestCase):
    def test_json_report_for_a_slurm_cluster(self) -> None:
        table = {
            ("sinfo", "--version"): (0, "slurm 23.11.4\n", ""),
            ("sinfo", "-h", "-o", "%P|%a|%l|%D|%c|%m|%G|%f"): (0, SINFO, ""),
            ("sinfo", "-h", "-N", "-o", "%P|%a|%t|%D|%N"): (0, SINFO_N, ""),
            ("scontrol", "show", "partition"): (0, SCONTROL, ""),
        }
        commands = {name: f"/usr/bin/{name}" for name in ("sbatch", "sinfo", "squeue", "scontrol")}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Submission").mkdir()
            (root / "Submission" / "submitJob_local.py").write_text(
                'PARTITION = "gpu"\n', encoding="utf-8"
            )
            with patch.object(cs, "Probe", lambda workdir=None: FakeProbe(table)), patch.object(
                cs, "_which", lambda names: commands
            ), patch("sys.stdout") as out:
                code = cs.main(["--json", "--workdir", str(root)])
                payload = json.loads("".join(call.args[0] for call in out.write.call_args_list))
        self.assertEqual(code, 0)
        self.assertEqual(payload["schedulers"][0]["family"], "slurm")
        self.assertTrue(any(queue["available"] for queue in payload["schedulers"][0]["queues"]))
        self.assertTrue(any("gpu" in item for item in payload["submission_request"]))

    def test_no_client_reports_unknown_and_exits_2(self) -> None:
        with patch.object(cs, "Probe", lambda workdir=None: FakeProbe({})), patch.object(
            cs, "_which", lambda names: {}
        ), patch("sys.stdout"):
            code = cs.main([])
        self.assertEqual(code, 2)

    def test_client_without_queues_exits_3(self) -> None:
        table = {
            ("sinfo", "--version"): (0, "slurm 23.11.4\n", ""),
            ("sinfo", "-h", "-o", "%P|%a|%l|%D|%c|%m|%G|%f"): (1, "", "sinfo: error\n"),
            ("sinfo", "-h", "-N", "-o", "%P|%a|%t|%D|%N"): (1, "", "sinfo: error\n"),
        }
        commands = {name: f"/usr/bin/{name}" for name in ("sbatch", "sinfo", "squeue")}
        with patch.object(cs, "Probe", lambda workdir=None: FakeProbe(table)), patch.object(
            cs, "_which", lambda names: commands
        ), patch("sys.stdout"):
            code = cs.main([])
        self.assertEqual(code, 3)

    def test_test_submit_is_slurm_only(self) -> None:
        table = {
            ("qstat", "--version"): (0, "PBS Pro 2021.1.3\n", ""),
            ("qstat", "-Q"): (0, QSTAT_Q, ""),
            ("qstat", "-q"): (0, QSTAT_PER_QUEUE, ""),
        }
        commands = {name: f"/opt/pbs/bin/{name}" for name in ("qsub", "qstat")}
        with patch.object(cs, "Probe", lambda workdir=None: FakeProbe(table)), patch.object(
            cs, "_which", lambda names: commands
        ), patch("sys.stdout") as out:
            cs.main(["--test-submit"])
        printed = "\n".join(call.args[0] for call in out.write.call_args_list)
        self.assertIn("skipped for non-Slurm", printed)


if __name__ == "__main__":
    unittest.main()
