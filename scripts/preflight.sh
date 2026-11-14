#!/usr/bin/env bash
# Pre-submission preflight — the hard gates of docs/submission-checklist.md §2 in one command.
#
#   ./scripts/preflight.sh              # full: 7 gates, ~3 min (the test suite is the slow one)
#   ./scripts/preflight.sh --fast       # skip the two slow gates (pytest suite, clone reproduction)
#   ./scripts/preflight.sh --json       # machine-readable table for CI / a script
#   ./scripts/preflight.sh --processed DIR   # artefact root to gate (default data/processed)
#
# Answers one question: "are we ready to submit?" Every executed gate records exit code and wall
# clock, a failing gate never aborts the remaining ones, and the exit status is non-zero only if an
# executed gate failed (skips are reported, not failed). Nothing is written inside the repo: all
# scratch files live under a mktemp -d directory removed by the EXIT trap. The clone gate is a
# read-only `git clone` of the committed state — it never writes to this repo's git data.
#
# set -e is on at the top level on purpose; inside run_gate() it is suspended so a failing gate is
# measured and recorded instead of killing the run (see the comment there).
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

FAST=0
JSON=0
PROCESSED=data/processed

usage() {
  sed -n '4,13p' "$0" | sed 's/^# \{0,1\}//'
}

while (($#)); do
  case $1 in
    --fast) FAST=1 ;;
    --json) JSON=1 ;;
    --processed)
      [[ $# -ge 2 ]] || { echo "error: --processed needs a directory" >&2; exit 2; }
      PROCESSED=$2
      shift
      ;;
    --processed=*)
      PROCESSED=${1#--processed=}
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "error: unknown argument '$1' (try --help)" >&2
      exit 2
      ;;
  esac
  shift
done

TMP_PF=$(mktemp -d "${TMPDIR:-/tmp}/preflight.XXXXXXXXXX")
LOGS=$TMP_PF/logs
mkdir -p "$LOGS"
cleanup() { rm -rf "$TMP_PF"; }
trap cleanup EXIT HUP INT TERM

# Bytecode caches would land in the repo; the gates are read-only apart from this.
export PYTHONDONTWRITEBYTECODE=1

# The figures gate always writes here; docs/figures/ is never passed to --out.
FIG_OUT=$TMP_PF/figures

now_ns() {
  local t=${EPOCHREALTIME:-}
  t=${t/,/.}
  if [[ $t == *.* ]]; then
    # EPOCHREALTIME is seconds.microseconds; pad the fraction to nanoseconds.
    local secs=${t%%.*} frac=${t#*.}
    frac=${frac}000000000
    printf '%s%s' "$secs" "${frac:0:9}"
  elif date +%s%N >/dev/null 2>&1; then
    date +%s%N
  else
    printf '%s000000000' "$SECONDS"
  fi
}

fmt_secs() { # milliseconds -> seconds with two decimals
  local ms=$1
  ((ms < 0)) && ms=0
  printf '%d.%02d' "$((ms / 1000))" "$(((ms % 1000) / 10))"
}

clip() { # string [max] — keep the table readable
  local s=$1 max=${2:-52}
  if ((${#s} > max)); then
    printf '%s…' "${s:0:max-1}"
  else
    printf '%s' "$s"
  fi
}

json_escape() {
  local s=$1
  s=${s//\\/\\\\}
  s=${s//\"/\\\"}
  s=${s//$'\n'/\\n}
  s=${s//$'\r'/\\r}
  s=${s//$'\t'/\\t}
  printf '%s' "$s"
}

# ── rows ────────────────────────────────────────────────────────────────────────────────────────
declare -a R_ID=() R_NAME=() R_STATUS=() R_EXIT=() R_MS=() R_EV=()

add_row() {
  R_ID+=("$1")
  R_NAME+=("$2")
  R_STATUS+=("$3")
  R_EXIT+=("$4")
  R_MS+=("$5")
  R_EV+=("$6")
}

record_skip() {
  add_row "$1" "$2" SKIP "-" 0 "$3"
}

progress() {
  ((JSON)) || printf '▶ %s\n' "$*" >&2
}

# Gate functions set G_STATUS / G_EXIT / G_EVIDENCE / G_LOG and always return 0: run_gate decides
# the outcome from G_STATUS, and a later gate must still run after an earlier one fails.
G_STATUS=FAIL
G_EXIT=-
G_EVIDENCE=""
G_LOG=""

run_gate() {
  local id=$1 name=$2 fn=$3
  local t0 t1 ms
  G_STATUS=FAIL G_EXIT=- G_EVIDENCE="" G_LOG=""
  progress "[$id/7] $name"
  t0=$(now_ns)
  # errexit stays off for the duration of the gate: the point is to record the failure and keep
  # going, then restore strict mode for the bookkeeping below.
  set +e
  "$fn"
  set -e
  t1=$(now_ns)
  ms=$(((t1 - t0) / 1000000))
  ((ms < 0)) && ms=0
  add_row "$id" "$name" "$G_STATUS" "$G_EXIT" "$ms" "$G_EVIDENCE"
  if [[ $G_STATUS == FAIL ]]; then
    {
      printf '\n── FAIL %s (exit %s) — last 25 lines of the gate log:\n' "$name" "$G_EXIT"
      if [[ -n $G_LOG && -f $G_LOG ]]; then
        tail -n 25 "$G_LOG"
      else
        printf '(no log captured)\n'
      fi
    } >&2
  fi
  return 0
}

# ── context (informational; not a gate) ────────────────────────────────────────────────────────
COMMIT=$(git rev-parse --short HEAD 2>/dev/null || printf 'unknown')
DIRTY_PATHS=$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')

# ── gate 1: contract self-check ────────────────────────────────────────────────────────────────
gate_contracts() {
  G_LOG=$LOGS/01-contracts.log
  CONTRACT_FP=""
  ./scripts/python.sh -m src.domain.contracts >"$G_LOG" 2>&1
  G_EXIT=$?
  local fp
  fp=$(sed -n 's/^contract fingerprint: //p' "$G_LOG" | head -1)
  G_STATUS=PASS
  if [[ -z $fp ]]; then
    G_STATUS=FAIL
    G_EVIDENCE="no 'contract fingerprint:' line"
  else
    CONTRACT_FP=${fp:0:8}
    G_EVIDENCE="fingerprint $CONTRACT_FP"
  fi
  ((G_EXIT == 0)) || G_STATUS=FAIL
  return 0
}

# ── gate 2: full test suite ────────────────────────────────────────────────────────────────────
gate_pytest() {
  G_LOG=$LOGS/02-pytest.log
  ./scripts/python.sh -m pytest tests -q >"$G_LOG" 2>&1
  G_EXIT=$?
  if ((G_EXIT == 0)); then
    G_STATUS=PASS
    # pytest.ini adds a second -q, so pytest prints no count; the exit code is the evidence.
    G_EVIDENCE="suite exit 0 (pytest -q -q: count suppressed)"
  else
    G_STATUS=FAIL
    local first
    first=$(grep -m1 -E '^(FAILED|ERROR) ' "$G_LOG" || true)
    G_EVIDENCE="exit $G_EXIT${first:+ — ${first:0:40}}"
  fi
  return 0
}

# ── gate 3: artefact gate + size budget ────────────────────────────────────────────────────────
gate_artifacts() {
  G_LOG=$LOGS/03-artifacts.log
  TREE_SIZE=""
  ./scripts/python.sh pipeline/validate_artifacts.py \
    --processed "$PROCESSED" --strict-size-mb 15 >"$G_LOG" 2>&1
  G_EXIT=$?
  TREE_SIZE=$(sed -n 's/^  total size: //p' "$G_LOG" | head -1)
  G_STATUS=PASS
  ((G_EXIT == 0)) || G_STATUS=FAIL
  if [[ -n $TREE_SIZE ]]; then
    G_EVIDENCE="total size: $TREE_SIZE"
  else
    G_STATUS=FAIL
    G_EVIDENCE="no 'total size:' line under $PROCESSED"
  fi
  return 0
}

# ── gate 4: phase A/B handoff ──────────────────────────────────────────────────────────────────
gate_phase_a() {
  G_LOG=$LOGS/04-phase-a.log
  PHASE_LINE=""
  ./scripts/check_phase_a.sh >"$G_LOG" 2>&1
  G_EXIT=$?
  PHASE_LINE=$(grep -E '^PHASE A/B:' "$G_LOG" | tail -1 || true)
  G_STATUS=PASS
  ((G_EXIT == 0)) || G_STATUS=FAIL
  if [[ -n $PHASE_LINE ]]; then
    G_EVIDENCE=${PHASE_LINE%% —*}
  else
    G_STATUS=FAIL
    G_EVIDENCE="no 'PHASE A/B:' verdict line"
  fi
  return 0
}

# ── gate 5: clone of the committed state ───────────────────────────────────────────────────────
gate_clone() {
  G_LOG=$LOGS/05-clone.log
  CLONE_SIZE=""
  local clone=$TMP_PF/clone
  if [[ ! -x .venv/bin/python ]]; then
    G_STATUS=SKIP
    G_EXIT=-
    G_EVIDENCE=".venv/bin/python missing — committed state not reproduced"
    printf '%s\n' "$G_EVIDENCE" >"$G_LOG"
    return 0
  fi

  local clone_rc=0
  progress "    git clone -q . $clone"
  git clone -q . "$clone" >"$G_LOG" 2>&1 || clone_rc=$?
  if ((clone_rc != 0)) || [[ ! -d $clone/.git ]]; then
    G_STATUS=SKIP
    G_EXIT=-
    local why
    why=$(tail -1 "$G_LOG" | tr -d '\n')
    G_EVIDENCE="git clone failed${why:+: $why}"
    [[ -n $why ]] || G_EVIDENCE="git clone failed (git unavailable or clone refused)"
    return 0
  fi
  ln -s "$ROOT/.venv" "$clone/.venv" >>"$G_LOG" 2>&1 || true

  local crc=0 vrc=0
  progress "    contracts + artefact gate inside the clone"
  (cd "$clone" && ./scripts/python.sh -m src.domain.contracts) \
    >"$TMP_PF/clone-contracts.log" 2>&1 || crc=$?
  (cd "$clone" && ./scripts/python.sh pipeline/validate_artifacts.py \
    --processed data/processed --strict-size-mb 15) \
    >"$TMP_PF/clone-gate.log" 2>&1 || vrc=$?

  local cfp csize
  cfp=$(sed -n 's/^contract fingerprint: //p' "$TMP_PF/clone-contracts.log" | head -1)
  csize=$(sed -n 's/^  total size: //p' "$TMP_PF/clone-gate.log" | head -1)
  CLONE_SIZE=$csize

  # One combined log so the failure tail shows both sub-runs.
  {
    printf '\n$ (clone) python -m src.domain.contracts  [exit %s]\n' "$crc"
    cat "$TMP_PF/clone-contracts.log"
    printf '\n$ (clone) validate_artifacts.py --strict-size-mb 15  [exit %s]\n' "$vrc"
    cat "$TMP_PF/clone-gate.log"
  } >>"$G_LOG"
  G_LOG=$TMP_PF/05-clone.log

  G_STATUS=PASS
  ((crc == 0 && vrc == 0)) || G_STATUS=FAIL
  if [[ $G_STATUS == PASS && -n $cfp && -n $csize ]]; then
    G_EXIT=0
    G_EVIDENCE="clone: fp ${cfp:0:8}, $csize"
  elif [[ $G_STATUS == PASS ]]; then
    G_STATUS=FAIL
    G_EVIDENCE="clone passed but printed no fingerprint/size line"
  else
    G_EXIT=$((crc != 0 ? crc : vrc))
    G_EVIDENCE="clone: contracts exit $crc, gate exit $vrc"
  fi
  return 0
}

# ── gate 6: figures regenerate cleanly ────────────────────────────────────────────────────────
gate_figures() {
  G_LOG=$LOGS/06-figures.log
  ./scripts/python.sh scripts/make_figures.py --out "$FIG_OUT" --processed "$PROCESSED" \
    >"$G_LOG" 2>&1
  G_EXIT=$?

  local png found=0 missing=""
  for png in velocity_shared_scale.png timeseries_three_cities.png coherence.png; do
    if [[ -f $FIG_OUT/$png ]]; then
      found=$((found + 1))
    else
      missing+="$png "
    fi
  done

  G_STATUS=PASS
  ((G_EXIT == 0 && found == 3)) || G_STATUS=FAIL
  if ((G_EXIT != 0)); then
    G_EVIDENCE="make_figures exit $G_EXIT${missing:+, missing: ${missing% }}"
  elif ((found == 3)); then
    G_EVIDENCE="3/3 PNGs in temp dir (--out; docs/figures/ not used)"
  else
    G_EVIDENCE="missing PNG(s): ${missing% }"
  fi
  return 0
}

# ── gate 7: demo numbers ──────────────────────────────────────────────────────────────────────
gate_demo() {
  G_LOG=$LOGS/07-demo.log
  local out=$TMP_PF/demo-numbers.json
  local drc=0 vrc=0 summary=""
  ./scripts/python.sh scripts/demo_numbers.py --json --processed "$PROCESSED" \
    >"$out" 2>>"$G_LOG" || drc=$?

  summary=$(./scripts/python.sh - "$out" "$PROCESSED" <<'PY' 2>>"$G_LOG"
import json
import pathlib
import sys

doc_path = pathlib.Path(sys.argv[1])
root = pathlib.Path(sys.argv[2])
try:
    doc = json.loads(doc_path.read_text())
except (OSError, json.JSONDecodeError) as exc:
    print(f"demo JSON unparsable: {exc}")
    raise SystemExit(1)

cases = [str(case.get("case_id")) for case in doc.get("cases") or []]
expected = (
    sorted(
        path.name
        for path in root.iterdir()
        if path.is_dir() and not path.name.startswith(("_", "."))
    )
    if root.is_dir()
    else []
)
if not expected:
    print(f"no case directories under {root}")
    raise SystemExit(1)
missing = [case for case in expected if case not in cases]
if missing:
    print(f"missing case(s) in demo JSON: {', '.join(missing)}")
    raise SystemExit(1)
if len(cases) != len(expected):
    print(f"demo JSON has {len(cases)} case(s), expected {len(expected)}")
    raise SystemExit(1)
print(f"{len(cases)}/{len(expected)} cases: {', '.join(cases)}")
PY
) || vrc=$?

  {
    printf '\n$ demo_numbers.py --json --processed %s  [exit %s]\n' "$PROCESSED" "$drc"
    printf 'checker [exit %s]: %s\n' "$vrc" "$summary"
  } >>"$G_LOG"

  G_STATUS=PASS
  ((drc == 0 && vrc == 0)) || G_STATUS=FAIL
  G_EXIT=$((drc != 0 ? drc : vrc))
  if [[ -z $summary ]]; then
    summary="checker printed no summary"
  fi
  G_EVIDENCE=$summary
  return 0
}

# ── run ───────────────────────────────────────────────────────────────────────────────────────
TOTAL_T0=$(now_ns)
run_gate 1 "contract self-check" gate_contracts

if ((FAST)); then
  record_skip 2 "pytest suite (slow)" "--fast: not executed (the ~2.5 min gate)"
else
  run_gate 2 "pytest suite (slow)" gate_pytest
fi

run_gate 3 "artefact gate (size)" gate_artifacts
run_gate 4 "phase A/B handoff" gate_phase_a

if ((FAST)); then
  record_skip 5 "clone reproduction (slow)" "--fast: not executed (needs a clone + slow imports)"
else
  run_gate 5 "clone reproduction (slow)" gate_clone
fi

run_gate 6 "figures regenerate" gate_figures
run_gate 7 "demo numbers" gate_demo
TOTAL_MS=$((($(now_ns) - TOTAL_T0) / 1000000))

# ── classify ─────────────────────────────────────────────────────────────────────────────────
declare -a FAILED=() SKIPPED=()
for i in "${!R_ID[@]}"; do
  case ${R_STATUS[$i]} in
    FAIL) FAILED+=("${R_NAME[$i]}") ;;
    SKIP) SKIPPED+=("${R_NAME[$i]}") ;;
  esac
done
FAIL_COUNT=${#FAILED[@]}
EXECUTED=$((7 - ${#SKIPPED[@]}))
if ((FAIL_COUNT == 0)); then
  VERDICT=READY
else
  VERDICT="NOT READY"
fi

# Informational notes: the checklist also wants a clean tree and figures newer than the artefacts.
NOTES=()
if [[ -n $DIRTY_PATHS && $DIRTY_PATHS != 0 ]]; then
  NOTES+=("tree is dirty ($DIRTY_PATHS changed paths) — checklist §2 wants the commit you tested")
fi
if [[ -n ${CLONE_SIZE:-} && -n ${TREE_SIZE:-} && $CLONE_SIZE != "$TREE_SIZE" ]]; then
  NOTES+=("committed artefacts differ from your tree (clone ${CLONE_SIZE%% / *} vs tree ${TREE_SIZE%% / *})")
fi

# ── output ───────────────────────────────────────────────────────────────────────────────────
if ((JSON)); then
  printf '{\n'
  printf '  "generated_by": "scripts/preflight.sh",\n'
  printf '  "repo": "%s",\n' "$(json_escape "$ROOT")"
  printf '  "commit": "%s",\n' "$(json_escape "$COMMIT")"
  printf '  "dirty_paths": %s,\n' "${DIRTY_PATHS:-0}"
  printf '  "mode": "%s",\n' "$([[ $FAST == 1 ]] && printf fast || printf full)"
  printf '  "processed": "%s",\n' "$(json_escape "$PROCESSED")"
  printf '  "verdict": "%s",\n' "$VERDICT"
  printf '  "ready": %s,\n' "$([[ $FAIL_COUNT == 0 ]] && printf true || printf false)"
  printf '  "executed": %d,\n' "$EXECUTED"
  printf '  "total_seconds": %s,\n' "$(fmt_secs "$TOTAL_MS")"
  printf '  "failed": ['
  for i in "${!FAILED[@]}"; do
    ((i)) && printf ', '
    printf '"%s"' "$(json_escape "${FAILED[$i]}")"
  done
  printf '],\n'
  printf '  "skipped": ['
  for i in "${!SKIPPED[@]}"; do
    ((i)) && printf ', '
    printf '"%s"' "$(json_escape "${SKIPPED[$i]}")"
  done
  printf '],\n'
  printf '  "notes": ['
  for i in "${!NOTES[@]}"; do
    ((i)) && printf ', '
    printf '"%s"' "$(json_escape "${NOTES[$i]}")"
  done
  printf '],\n'
  printf '  "steps": [\n'
  for i in "${!R_ID[@]}"; do
    local_exit=${R_EXIT[$i]}
    if [[ $local_exit == '-' ]]; then local_exit=null; fi
    printf '    {"id": %s, "name": "%s", "status": "%s", "exit": %s, "seconds": %s, "evidence": "%s"}' \
      "${R_ID[$i]}" "$(json_escape "${R_NAME[$i]}")" "${R_STATUS[$i]}" \
      "$local_exit" "$(fmt_secs "${R_MS[$i]}")" "$(json_escape "${R_EV[$i]}")"
    ((i + 1 < ${#R_ID[@]})) && printf ','
    printf '\n'
  done
  printf '  ]\n'
  printf '}\n'
  ((FAIL_COUNT == 0)) || exit 1
  exit 0
fi

# Text table.
mode_label="full (7 gates)"
((FAST)) && mode_label="fast (gates 2 and 5 skipped)"
printf 'preflight — %s\n' "$ROOT"
printf 'commit %s · tree: %s · processed: %s · mode: %s\n' \
  "$COMMIT" \
  "$([[ ${DIRTY_PATHS:-0} == 0 ]] && printf clean || printf '%s changed paths' "$DIRTY_PATHS")" \
  "$PROCESSED" "$mode_label"
printf '\n'

printf '%-2s  %-26s  %-6s  %-4s  %8s  %s\n' "#" "gate" "status" "exit" "secs" "evidence"
printf '%-2s  %-26s  %-6s  %-4s  %8s  %s\n' "--" "--------------------------" "------" "----" "--------" "--------"
for i in "${!R_ID[@]}"; do
  printf '%-2s  %-26s  %-6s  %-4s  %8s  %s\n' \
    "${R_ID[$i]}" "${R_NAME[$i]}" "${R_STATUS[$i]}" "${R_EXIT[$i]}" \
    "$(fmt_secs "${R_MS[$i]}")" "$(clip "${R_EV[$i]}" 58)"
done

printf '\n'
for note in "${NOTES[@]}"; do
  printf 'note: %s\n' "$note"
done

if ((FAIL_COUNT == 0)); then
  printf 'VERDICT: READY — %d/%d executed gates passed in %s s' \
    "$EXECUTED" "$EXECUTED" "$(fmt_secs "$TOTAL_MS")"
  if ((${#SKIPPED[@]})); then
    printf ' (%d skipped: %s)' "${#SKIPPED[@]}" "$(printf '%s, ' "${SKIPPED[@]}" | sed 's/, $//')"
  fi
  printf '\n'
  exit 0
fi

printf 'VERDICT: NOT READY — %d of %d executed gates failed in %s s\n' \
  "$FAIL_COUNT" "$EXECUTED" "$(fmt_secs "$TOTAL_MS")"
printf '  failed: %s\n' "$(printf '%s, ' "${FAILED[@]}" | sed 's/, $//')"
if ((${#SKIPPED[@]})); then
  printf '  skipped: %s\n' "$(printf '%s, ' "${SKIPPED[@]}" | sed 's/, $//')"
fi
printf '  fix the gates above before submitting; the failure tails are on stderr.\n'
exit 1
