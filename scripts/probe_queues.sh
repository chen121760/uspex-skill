#!/bin/sh
# Read-only PBS/legacy queue probe for hosts without Python 3.
#
# Answers, using only the scheduler's own client commands:
#   1. which PBS server am I talking to
#   2. every queue and its Ena/Str (enabled/started) flags
#   3. per-queue attributes and current load
#   4. node/queue health, when pbsnodes exists
#
# It never submits a job and never edits anything.
#
# Usage: sh probe_queues.sh [RUN_DIR]

set -u

echo "=== which server (qstat -B) ==="
qstat -B 2>&1 | head -5

echo
echo "=== queues and their enabled/started flags (qstat -Q) ==="
qstat -Q 2>&1

echo
echo "=== availability verdict ==="
# The authoritative availability columns in qstat -Q are Ena and Str, wherever the
# server puts the remaining columns.  A queue is unusable when either of them is "no".
qstat -Q 2>&1 | awk '
    function lower(value) { return tolower(value) }
    /^[ ]*Queue[ ]/ { header = 1; for (i = 1; i <= NF; i++) name_col[i] = $i; next }
    header && /^-/ { next }
    header && NF > 1 && $1 ~ /^[A-Za-z]/ {
        ena = ""; str = ""
        for (i = 1; i <= NF; i++) {
            if (name_col[i] == "Ena") ena = $(i)
            if (name_col[i] == "Str") str = $(i)
        }
        verdict = "available"
        why = ""
        if (lower(ena) == "no" || lower(ena) == "false") { verdict = "NOT available"; why = "Ena=" ena }
        if (lower(str) == "no" || lower(str) == "false") {
            verdict = "NOT available"
            why = (why == "" ? "" : why "; ") "Str=" str
        }
        printf "  - %s: %s%s\n", $1, verdict, (why == "" ? "" : " (" why ")")
    }
'

echo
echo "=== per-queue detail (qstat -q) ==="
qstat -q 2>&1

echo
echo "=== node health (pbsnodes) ==="
if command -v pbsnodes >/dev/null 2>&1; then
    # Node names may carry a domain suffix (node10.cluster); a node block header is
    # a bare single token with no " = " in it.
    pbsnodes -a 2>/dev/null | awk '
        /^[^ =]+$/ && NF == 1 { node = $1; nodes++; next }
        # Only a line whose whole value is the state counts: the GridView dispatcher
        # also emits "status = ...,state=free,netload=..." which must be ignored.
        /^[ \t]*state[ \t]*=[ \t]*[^, \t]+[ \t]*$/ {
            s = $0; sub(/^[ \t]*state[ \t]*=[ \t]*/, "", s); sub(/[ \t]*$/, "", s); states[s]++
        }
        /^[ \t]*queue[ \t]*=[ \t]*[^, \t]+[ \t]*$/ {
            q = $0; sub(/^[ \t]*queue[ \t]*=[ \t]*/, "", q); sub(/[ \t]*$/, "", q); queued[q]++; assigned++
        }
        END {
            printf "  nodes listed: %d\n", nodes
            for (s in states) printf "  state %s: %d\n", s, states[s]
            if (assigned == 0)
                print "  this server does not declare a node-to-queue mapping (no \"queue =\" line),"
            else
                for (q in queued) printf "  queue %s: %d node(s)\n", q, queued[q]
        }
    '
    echo "  queue health must therefore come from qstat (Ena/Str), not from pbsnodes here."
else
    echo "  pbsnodes is not on PATH on this host; per-queue node health was not checked."
    echo "  Availability above therefore comes from qstat attributes (Ena/Str) only."
fi

echo
echo "=== queue declared by a run's Submission/ (optional) ==="
if [ $# -ge 1 ] && [ -d "$1/Submission" ]; then
    grep -nE '^[[:space:]]*(QUEUE|PARTITION|RESOURCE_LINE)[[:space:]]*=' "$1"/Submission/*.py 2>/dev/null \
        || echo "  no queue declared in $1/Submission"
else
    echo "  (pass a run directory as the first argument to inspect Submission/)"
fi
