#!/bin/sh

set -eu

PROGRAM_NAME=${0##*/}
SCRIPT_DIRECTORY=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
REPOSITORY_ROOT=$(CDPATH='' cd -- "$SCRIPT_DIRECTORY/.." && pwd)
TEST_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/config-health-tests.XXXXXX")
CERTBOT_HEALTHCHECK=$REPOSITORY_ROOT/deploy/certbot-healthcheck
HOST_VERIFIER=$REPOSITORY_ROOT/deploy/verify-deployment

cleanup() {
    if [ -d "$TEST_ROOT" ]; then
        rm -rf -- "$TEST_ROOT"
    fi
}

fail() {
    printf '%s: %s\n' "$PROGRAM_NAME" "$*" >&2
    exit 1
}

expect_failure() {
    expected_message=$1
    shift

    if "$@" >"$TEST_ROOT/command.stdout" 2>"$TEST_ROOT/command.stderr"; then
        fail "command unexpectedly succeeded: $*"
    fi
    if [ -n "$expected_message" ]; then
        grep -F -- "$expected_message" "$TEST_ROOT/command.stderr" >/dev/null \
            || fail "command failed without expected message: $expected_message"
    fi
}

extract_function() {
    function_name=$1
    source_file=$2
    sed -n "/^${function_name}() {/,/^}/p" "$source_file"
}

trap cleanup EXIT HUP INT TERM

command -v jq >/dev/null 2>&1 || fail "jq is required for health-verifier tests"
command -v openssl >/dev/null 2>&1 || fail "openssl is required for health-verifier tests"

mkdir -p "$TEST_ROOT/empty-live" "$TEST_ROOT/live/example.test" "$TEST_ROOT/invalid-live/bad.test"

expect_failure \
    'live directory must be an existing real directory' \
    "$CERTBOT_HEALTHCHECK" --live-dir "$TEST_ROOT/missing-live"
expect_failure \
    'no certificates found under live directory' \
    "$CERTBOT_HEALTHCHECK" --live-dir "$TEST_ROOT/empty-live"
expect_failure \
    '--warn-days must be an integer' \
    "$CERTBOT_HEALTHCHECK" --live-dir "$TEST_ROOT/empty-live" --warn-days nope
expect_failure \
    'unsupported format' \
    "$CERTBOT_HEALTHCHECK" --live-dir "$TEST_ROOT/empty-live" --format yaml

openssl req -x509 -newkey rsa:2048 -sha256 -nodes -days 90 \
    -subj '/CN=example.test/O=Mikinho Healthcheck Test' \
    -keyout "$TEST_ROOT/certificate.key" \
    -out "$TEST_ROOT/live/example.test/fullchain.pem" >/dev/null 2>&1

"$CERTBOT_HEALTHCHECK" \
    --live-dir "$TEST_ROOT/live" \
    --warn-days 1 > "$TEST_ROOT/report.txt"
grep -F '[OK]' "$TEST_ROOT/report.txt" >/dev/null
grep -F 'Subject:' "$TEST_ROOT/report.txt" >/dev/null
grep -F 'Issuer:' "$TEST_ROOT/report.txt" >/dev/null

"$CERTBOT_HEALTHCHECK" \
    --live-dir "$TEST_ROOT/live" \
    --warn-days 1 \
    --format json > "$TEST_ROOT/report.json"
jq -e '
    .total == 1
    and .expiring == 0
    and .certificates[0].domain == "example.test"
    and (.certificates[0].subject | type == "string" and length > 0)
    and (.certificates[0].issuer | type == "string" and length > 0)
    and (.certificates[0].remaining_seconds | type == "number" and . > 0)
' "$TEST_ROOT/report.json" >/dev/null

"$CERTBOT_HEALTHCHECK" \
    --live-dir "$TEST_ROOT/live" \
    --warn-days 1 \
    --format prometheus > "$TEST_ROOT/report.prom"
grep -Eq '^certbot_certificate_expiry_seconds\{domain="example[.]test"\} [0-9]+$' \
    "$TEST_ROOT/report.prom"

"$CERTBOT_HEALTHCHECK" \
    --live-dir "$TEST_ROOT/live" \
    --warn-days 1 \
    --output "$TEST_ROOT/published-report.json" \
    --format json
if [ ! -f "$TEST_ROOT/published-report.json" ] || [ -L "$TEST_ROOT/published-report.json" ]; then
    fail "healthcheck did not atomically publish a regular output file"
fi
jq -e '.total == 1' "$TEST_ROOT/published-report.json" >/dev/null

: > "$TEST_ROOT/output-victim"
ln -s "$TEST_ROOT/output-victim" "$TEST_ROOT/symlink-output"
expect_failure \
    'refusing symbolic-link output' \
    "$CERTBOT_HEALTHCHECK" \
    --live-dir "$TEST_ROOT/live" \
    --warn-days 1 \
    --output "$TEST_ROOT/symlink-output"
[ ! -s "$TEST_ROOT/output-victim" ] || fail "healthcheck followed a symbolic-link output"

printf '%s\n' 'not a certificate' > "$TEST_ROOT/invalid-live/bad.test/fullchain.pem"
expect_failure \
    'could not parse certificate expiry' \
    "$CERTBOT_HEALTHCHECK" --live-dir "$TEST_ROOT/invalid-live"
expect_failure \
    '' \
    "$CERTBOT_HEALTHCHECK" --live-dir "$TEST_ROOT/live" --warn-days 3650

require_root_function=$(extract_function require_root "$HOST_VERIFIER")
root_result=$(sh -c "
FAILED_CHECKS=0
fail_msg() { FAILED_CHECKS=\$((FAILED_CHECKS + 1)); }
id() { printf '501\\n'; }
$require_root_function
require_root || :
printf '%s\\n' \"\$FAILED_CHECKS\"
")
[ "$root_result" = 1 ] || fail "host verifier did not reject a non-root caller"

mkdir -p "$TEST_ROOT/mock-bin"
for tool_name in selinuxenabled getenforce sestatus semanage getsebool; do
    # The mock must inspect its own invoked basename at test runtime.
    # shellcheck disable=SC2016
    printf '#!/bin/sh\n[ "${0##*/}" != selinuxenabled ]\n' \
        > "$TEST_ROOT/mock-bin/$tool_name"
    chmod 0755 "$TEST_ROOT/mock-bin/$tool_name"
done
selinux_function=$(extract_function check_selinux "$HOST_VERIFIER")
selinux_result=$(PATH="$TEST_ROOT/mock-bin:$PATH" sh -c "
FAILED_CHECKS=0
fail_msg() { FAILED_CHECKS=\$((FAILED_CHECKS + 1)); }
pass_msg() { :; }
$selinux_function
check_selinux >/dev/null
printf '%s\\n' \"\$FAILED_CHECKS\"
")
[ "$selinux_result" = 1 ] || fail "disabled SELinux did not record exactly one failure"

backend_function=$(extract_function resolve_certbot_backend "$HOST_VERIFIER")
backend_result=$(sh -c "
FAILED_CHECKS=0
fail_msg() { FAILED_CHECKS=\$((FAILED_CHECKS + 1)); }
pass_msg() { :; }
BACKEND=auto
CERTBOT_SNAP_RUNNER_DROPIN=$TEST_ROOT/snap-runner.conf
$backend_function
: > \"\$CERTBOT_SNAP_RUNNER_DROPIN\"
resolve_certbot_backend
printf '%s ' \"\$RESOLVED_BACKEND\"
rm -f -- \"\$CERTBOT_SNAP_RUNNER_DROPIN\"
resolve_certbot_backend
printf '%s\\n' \"\$RESOLVED_BACKEND\"
")
[ "$backend_result" = 'snap native' ] \
    || fail "Certbot backend auto-detection returned: $backend_result"

printf 'Validated strict host and certificate healthcheck behavior.\n'
