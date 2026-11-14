#!/usr/bin/env bash
# Run the project venv's Python with the shared libraries that nixpkgs' interpreter does not
# put on the default search path (pip wheels still expect libstdc++.so.6, libz.so.1, ...).
#
#   ./scripts/python.sh -m src.domain.contracts
#   ./scripts/python.sh pipeline/validate_artifacts.py --case mexico-city
#
# Everything else in the repo should call Python through this wrapper, so nobody has to remember
# the LD_LIBRARY_PATH incantation.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -x .venv/bin/python ]]; then
  echo "error: .venv/bin/python missing. Create it with:" >&2
  echo "  python3 -m venv .venv" >&2
  echo "  ./scripts/python.sh -m pip install -r requirements.txt   # or uv pip install -r ..." >&2
  exit 1
fi

libs=()
# newest gcc runtime first; globs expand to nothing when absent, which is fine
# Pick 64-bit runtimes only: the store also carries ELFCLASS32 builds that poison the path.
for candidate in /nix/store/*-gcc-*-lib/lib /nix/store/*-zlib-1*/lib; do
  [[ -d "$candidate" ]] || continue
  for probe in "$candidate"/libstdc++.so.6 "$candidate"/libz.so.1; do
    if [[ -e "$probe" ]] && readelf -h "$probe" 2>/dev/null | grep -q 'ELF64'; then
      libs+=("$candidate")
      break
    fi
  done
done
if ((${#libs[@]})); then
  joined=$(IFS=:; echo "${libs[*]}")
  export LD_LIBRARY_PATH="${joined}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi

exec .venv/bin/python "$@"
