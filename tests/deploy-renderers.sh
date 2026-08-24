#!/bin/sh

set -eu

PROGRAM_NAME=${0##*/}
SCRIPT_DIRECTORY=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
REPOSITORY_ROOT=$(CDPATH='' cd -- "$SCRIPT_DIRECTORY/.." && pwd)
TEST_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/config-deploy-tests.XXXXXX")

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

    grep -F -- "$expected_message" "$TEST_ROOT/command.stderr" >/dev/null \
        || fail "command failed without expected message: $expected_message"
}

trap cleanup EXIT HUP INT TERM

expect_failure \
    'invalid output directory' \
    "$REPOSITORY_ROOT/deploy/install-nginx" \
    --output "$TEST_ROOT/nginx-dot-parent/missing/."
expect_failure \
    'invalid output directory' \
    "$REPOSITORY_ROOT/deploy/install-nginx" \
    --output "$TEST_ROOT/nginx-parent/missing/.."
expect_failure \
    'invalid output directory' \
    "$REPOSITORY_ROOT/deploy/install-php-site" \
    --output "$TEST_ROOT/php-dot-parent/missing/." \
    --tag test_wp
expect_failure \
    'invalid output directory' \
    "$REPOSITORY_ROOT/deploy/install-php-site" \
    --output "$TEST_ROOT/php-parent/missing/.." \
    --tag test_wp

[ ! -e "$TEST_ROOT/nginx-dot-parent" ] \
    || fail 'nginx renderer created state for a rejected dot output'
[ ! -e "$TEST_ROOT/nginx-parent" ] \
    || fail 'nginx renderer created state for a rejected dot-dot output'
[ ! -e "$TEST_ROOT/php-dot-parent" ] \
    || fail 'PHP renderer created state for a rejected dot output'
[ ! -e "$TEST_ROOT/php-parent" ] \
    || fail 'PHP renderer created state for a rejected dot-dot output'
[ -x "$REPOSITORY_ROOT/deploy/verify-deployment" ] \
    || fail 'verify-deployment is not executable'
"$REPOSITORY_ROOT/deploy/verify-deployment" --help >/dev/null
expect_failure \
    'unknown argument: --invalid-option' \
    "$REPOSITORY_ROOT/deploy/verify-deployment" \
    --invalid-option

[ -x "$REPOSITORY_ROOT/deploy/certbot-healthcheck" ] \
    || fail 'certbot-healthcheck is not executable'
"$REPOSITORY_ROOT/deploy/certbot-healthcheck" --help >/dev/null
expect_failure \
    'unknown argument: --invalid-option' \
    "$REPOSITORY_ROOT/deploy/certbot-healthcheck" \
    --invalid-option

[ -x "$REPOSITORY_ROOT/deploy/install-host-tools" ] \
    || fail 'install-host-tools is not executable'
"$REPOSITORY_ROOT/deploy/install-host-tools" --help >/dev/null
"$REPOSITORY_ROOT/deploy/install-host-tools" --check >/dev/null

grep -Fq "geo \$wp_cache_revalidation_source {" \
    "$REPOSITORY_ROOT/nginx/stubs/http/fastcgi-cache.conf" \
    || fail 'WordPress cache revalidation is not source-restricted'
grep -Fq '"1:revalidate" 1;' \
    "$REPOSITORY_ROOT/nginx/stubs/http/fastcgi-cache.conf" \
    || fail 'WordPress cache revalidation lacks its exact control value'
if grep -Fq "map \$http_x_purge_key \$wp_has_purge_header" \
    "$REPOSITORY_ROOT/nginx/stubs/http/fastcgi-cache.conf"; then
    fail 'WordPress cache revalidation trusts arbitrary non-empty headers'
fi


"$REPOSITORY_ROOT/deploy/install-nginx" \
    --output "$TEST_ROOT/nginx-valid" \
    --profile gzip >/dev/null
"$REPOSITORY_ROOT/deploy/install-php-site" \
    --output "$TEST_ROOT/php-valid" \
    --tag test_wp >/dev/null

if find "$TEST_ROOT/nginx-valid" "$TEST_ROOT/php-valid" \
    -name '.config-render-*' -print | grep . >/dev/null; then
    fail 'renderer left an internal publication marker in its output'
fi

fixture_root=$TEST_ROOT/repository
mkdir -p "$fixture_root"
cp -R \
    "$REPOSITORY_ROOT/deploy" \
    "$REPOSITORY_ROOT/nginx" \
    "$REPOSITORY_ROOT/php-fpm" \
    "$REPOSITORY_ROOT/systemd" \
    "$fixture_root/"

rm "$fixture_root/nginx/includes/http.conf"
ln -s /etc/passwd "$fixture_root/nginx/includes/http.conf"
expect_failure \
    'nginx include source must be a regular file, not a symbolic link' \
    "$fixture_root/deploy/install-nginx" \
    --output "$TEST_ROOT/nginx-symlink"

rm "$fixture_root/php-fpm/pool/sample_wp.conf"
ln -s /etc/passwd "$fixture_root/php-fpm/pool/sample_wp.conf"
expect_failure \
    'source file must not be a symbolic link' \
    "$fixture_root/deploy/install-php-site" \
    --output "$TEST_ROOT/php-symlink" \
    --tag test_wp

printf 'Validated renderer output paths and source-file boundaries.\n'
