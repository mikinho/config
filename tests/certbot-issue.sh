#!/bin/sh

set -eu

PROGRAM_NAME=${0##*/}
SCRIPT_DIRECTORY=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
REPOSITORY_ROOT=$(CDPATH='' cd -- "$SCRIPT_DIRECTORY/.." && pwd)
ISSUER=$REPOSITORY_ROOT/certbot/issue
TEST_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/config-certbot-issue.XXXXXX")

cleanup() {
    if [ -d "$TEST_ROOT" ]; then
        rm -rf -- "$TEST_ROOT"
    fi
}

trap cleanup EXIT HUP INT TERM

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
    grep -F -- "$expected_message" "$TEST_ROOT/command.stderr" >/dev/null \
        || fail "command failed without expected message: $expected_message"
}

assert_contains() {
    checked_file=$1
    expected_text=$2
    grep -F -- "$expected_text" "$checked_file" >/dev/null \
        || fail "$checked_file is missing: $expected_text"
}

assert_not_contains() {
    checked_file=$1
    rejected_text=$2
    if grep -F -- "$rejected_text" "$checked_file" >/dev/null; then
        fail "$checked_file unexpectedly contains: $rejected_text"
    fi
}

[ -x "$ISSUER" ] || fail "Certbot issuer is not executable"
"$ISSUER" --help >/dev/null
"$ISSUER" --check >/dev/null

"$ISSUER" \
    --plan \
    --staging \
    --email ops@example.com \
    --domain Example.COM \
    --domain www.example.com \
    > "$TEST_ROOT/staging.plan"
assert_contains "$TEST_ROOT/staging.plan" 'environment=staging, backend=native, primary=example.com'
assert_contains "$TEST_ROOT/staging.plan" '/bin/certbot certonly --non-interactive --agree-tos'
assert_contains "$TEST_ROOT/staging.plan" '--preferred-challenges http'
assert_contains "$TEST_ROOT/staging.plan" '--domain example.com --domain www.example.com --dry-run'
assert_not_contains "$TEST_ROOT/staging.plan" 'certbot-healthcheck'

"$ISSUER" \
    --plan \
    --production \
    --staging-passed \
    --backend snap \
    --email ops@example.com \
    --domain example.com \
    > "$TEST_ROOT/production.plan"
assert_contains "$TEST_ROOT/production.plan" 'environment=production, backend=snap, primary=example.com'
assert_contains "$TEST_ROOT/production.plan" '/usr/local/bin/certbot certonly'
assert_contains "$TEST_ROOT/production.plan" \
    '/usr/local/bin/certbot renew --cert-name example.com --dry-run'
assert_contains "$TEST_ROOT/production.plan" '/usr/local/bin/certbot-healthcheck'
assert_not_contains "$TEST_ROOT/production.plan" '--domain example.com --dry-run'

expect_failure \
    'production issuance requires --staging-passed' \
    "$ISSUER" \
    --plan \
    --production \
    --email ops@example.com \
    --domain example.com
expect_failure \
    'select exactly one issuance environment' \
    "$ISSUER" \
    --plan \
    --email ops@example.com \
    --domain example.com
expect_failure \
    'at least one --domain is required' \
    "$ISSUER" --plan --staging --email ops@example.com
expect_failure \
    'duplicate DNS name' \
    "$ISSUER" \
    --plan \
    --staging \
    --email ops@example.com \
    --domain example.com \
    --domain EXAMPLE.COM
expect_failure \
    'wildcard names require a reviewed DNS-01 workflow' \
    "$ISSUER" \
    --plan \
    --staging \
    --email ops@example.com \
    --domain '*.example.com'
expect_failure \
    'invalid DNS label' \
    "$ISSUER" \
    --plan \
    --staging \
    --email ops@example.com \
    --domain bad-.example.com
expect_failure \
    'invalid ACME account email address' \
    "$ISSUER" \
    --plan \
    --staging \
    --email invalid \
    --domain example.com
expect_failure \
    'unsupported Certbot backend' \
    "$ISSUER" \
    --plan \
    --staging \
    --backend arbitrary \
    --email ops@example.com \
    --domain example.com
expect_failure \
    '--check does not accept issuance options' \
    "$ISSUER" --check --backend snap

printf 'Validated plan-first Certbot first-lineage issuance.\n'
