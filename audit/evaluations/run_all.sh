#!/bin/sh
# Run every audit probe against the checkout (or SUPERVICE_REPO if set).
# All probes are hermetic: each spawns its own daemon in its own /tmp dir and
# kills only pids it recorded itself, so the default set is safe to run beside
# live supervice instances. Exit is non-zero if any probe does not PASS.
set -u
cd "$(dirname "$0")"

pass=0; fail=0; error=0; failed_names=""
for probe in probe_*.py; do
    echo "== $probe"
    "${SUPERVICE_PYTHON:-python3}" "$probe"
    rc=$?
    case "$rc" in
        0) pass=$((pass + 1)) ;;
        2) error=$((error + 1)); failed_names="$failed_names $probe(ERROR)" ;;
        *) fail=$((fail + 1)); failed_names="$failed_names $probe" ;;
    esac
done

echo ""
echo "== probes: $pass PASS, $fail FAIL, $error ERROR"
if [ -n "$failed_names" ]; then
    echo "== not passing:$failed_names"
    exit 1
fi
