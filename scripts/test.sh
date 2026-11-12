#!/usr/bin/env bash
# Run every test suite (needs `pip install bpy`). PYTHON picks the interpreter.
set -euo pipefail
cd "$(dirname "$0")/.."
for t in tests/test_*.py; do
    "${PYTHON:-python}" "$t" > /tmp/papercraft_test.log 2>&1 \
        && echo "ok    $t" || { echo "FAIL  $t"; tail -20 /tmp/papercraft_test.log; exit 1; }
done
echo "All suites passed"
