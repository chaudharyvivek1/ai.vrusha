#!/usr/bin/env bash
#
# Run the whole pipeline offline, in a throwaway directory, and check that each
# stage produced what it promised. About 40 seconds on a laptop.
#
#   bash scripts/smoke_test.sh              # everything except training
#   bash scripts/smoke_test.sh --with-train # adds a 30-step tiny model
#
set -euo pipefail

cd "$(dirname "$0")/.."

WITH_TRAIN=0
for arg in "$@"; do
  [ "$arg" = "--with-train" ] && WITH_TRAIN=1
done

ROOT="$(mktemp -d)"
export VRUSHA_PATHS_ROOT="$ROOT"
trap 'rm -rf "$ROOT"' EXIT

hr() { printf '\n\033[1m== %s\033[0m\n' "$1"; }

hr "doctor"
python -m vrusha doctor

hr "harvest (offline fixtures, MIT)"
python -m vrusha harvest --dir examples/fixtures/pages --licence MIT

hr "passes"
python -m vrusha passes

hr "address tree"
python -m vrusha address

hr "retrieval only"
python -m vrusha query "why fuse keyword and vector search by rank" --top-k 3

hr "answer with citations"
python -m vrusha ask "why fuse keyword and vector search by rank"

hr "answer from code"
python -m vrusha ask "what does BoundedQueue.put return when the queue is full"

hr "refusal on a question the store cannot answer"
python -m vrusha ask "what is the airspeed velocity of an unladen swallow"

hr "tokenizer"
python -m vrusha tokenize --vocab 2048

hr "dataset"
python -m vrusha dataset

if [ "$WITH_TRAIN" = "1" ]; then
  hr "train (30 steps, tiny)"
  python -m vrusha train --preset tiny --steps 30 --batch-size 4 --seq-len 128 --eval-every 15
  hr "generate"
  python -m vrusha generate --prompt "Retrieval" --max-tokens 24
fi

hr "verify"
python -m vrusha verify

hr "report"
python -m vrusha report

hr "rules"
python -m vrusha rules --explain licence.allowed

hr "done"
echo "smoke test passed. Nothing was written outside $ROOT."
