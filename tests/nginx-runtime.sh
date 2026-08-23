#!/bin/sh

set -eu

PROGRAM_NAME=${0##*/}
NGINX_PREFIX=${1:-/tmp/nginx-safe}
NGINX_CONFIGURATION=nginx.conf
NGINX_PID_FILE=/run/nginx/nginx.pid
# The status site owns the exact 127.0.0.1:80 listener. Another address in
# Linux's loopback /8 reaches the wildcard listener without exposing CI ports.
PUBLIC_EDGE_ADDRESS=127.0.0.2
SAMPLE_ROOT=/var/www/sample_wp/wordpress
STATIC_FAILURE_LOG=/var/log/nginx/static-asset-failures.log
TEST_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/config-nginx-runtime.XXXXXX")
NGINX_PID=

cleanup() {
    if [ -n "$NGINX_PID" ] && kill -0 "$NGINX_PID" 2>/dev/null; then
        kill -QUIT "$NGINX_PID"
    fi

    rm -rf -- "$TEST_ROOT"
}

fail() {
    printf '%s: %s\n' "$PROGRAM_NAME" "$*" >&2
    exit 1
}

request() {
    request_name=$1
    shift

    curl \
        --connect-timeout 5 \
        --dump-header "$TEST_ROOT/$request_name.headers" \
        --max-time 10 \
        --noproxy '*' \
        --output "$TEST_ROOT/$request_name.body" \
        --silent \
        --show-error \
        --write-out '%{http_code}' \
        "$@"
}

assert_status() {
    expected_status=$1
    actual_status=$2
    request_name=$3

    [ "$actual_status" = "$expected_status" ] \
        || fail "$request_name returned $actual_status; expected $expected_status"
}

assert_header() {
    request_name=$1
    expected_header=$2

    tr -d '\r' <"$TEST_ROOT/$request_name.headers" \
        | grep --fixed-strings --ignore-case --line-regexp \
            -- "$expected_header" >/dev/null \
        || fail "$request_name omitted expected header: $expected_header"
}

stop_nginx() {
    if [ -z "$NGINX_PID" ] || ! kill -0 "$NGINX_PID" 2>/dev/null; then
        return
    fi

    kill -QUIT "$NGINX_PID"
    retry_count=0
    while kill -0 "$NGINX_PID" 2>/dev/null; do
        retry_count=$((retry_count + 1))
        [ "$retry_count" -lt 100 ] \
            || fail 'nginx did not stop after SIGQUIT'
        sleep 0.1
    done
    NGINX_PID=
}

trap cleanup EXIT HUP INT TERM

install -d -m 0755 "$SAMPLE_ROOT"
printf '%s\n' '<?php echo "application";' >"$SAMPLE_ROOT/index.php"
printf '%s\n' 'body { color: black; }' >"$SAMPLE_ROOT/app.css"
rm -f -- "$SAMPLE_ROOT/maintenance.html" "$STATIC_FAILURE_LOG"

/usr/sbin/nginx -p "$NGINX_PREFIX/" -c "$NGINX_CONFIGURATION"
NGINX_PID=$(cat "$NGINX_PID_FILE")
kill -0 "$NGINX_PID" 2>/dev/null \
    || fail 'nginx did not start'

status=$(request redirect \
    --resolve "example.com:80:$PUBLIC_EDGE_ADDRESS" \
    'http://example.com/path?retained=yes')
assert_status 308 "$status" redirect
assert_header redirect 'Location: https://example.com/path?retained=yes'

status=$(request proxied \
    --header 'Host: runtime-edge.invalid' \
    --header 'Proxy: http://attacker.invalid' \
    --header 'X-Forwarded-For: 198.51.100.7' \
    'http://127.0.0.1:18080/')
assert_status 200 "$status" proxied
grep --fixed-strings --line-regexp 'proxy=' "$TEST_ROOT/proxied.body" >/dev/null \
    || fail 'proxy request header was not removed upstream'
grep --fixed-strings --line-regexp 'xff=127.0.0.1' \
    "$TEST_ROOT/proxied.body" >/dev/null \
    || fail 'client-supplied forwarding chain was not replaced'
assert_header proxied "Content-Security-Policy: default-src 'none'"
csp_count=$(grep --ignore-case --count \
    '^Content-Security-Policy:' "$TEST_ROOT/proxied.headers" || true)
[ "$csp_count" -eq 1 ] \
    || fail "proxied response returned $csp_count Content-Security-Policy headers"

status=$(request maintenance_missing \
    --insecure \
    --resolve "example.com:443:$PUBLIC_EDGE_ADDRESS" \
    'https://example.com/index.php')
assert_status 503 "$status" maintenance_missing
assert_header maintenance_missing 'Strict-Transport-Security: max-age=31536000'
assert_header maintenance_missing 'X-Content-Type-Options: nosniff'

printf '%s\n' 'Temporarily unavailable' >"$SAMPLE_ROOT/maintenance.html"
status=$(request maintenance_present \
    --insecure \
    --resolve "example.com:443:$PUBLIC_EDGE_ADDRESS" \
    'https://example.com/index.php')
assert_status 502 "$status" maintenance_present
grep --fixed-strings 'Temporarily unavailable' \
    "$TEST_ROOT/maintenance_present.body" >/dev/null \
    || fail 'maintenance response did not serve the provisioned document'

status=$(request maintenance_direct \
    --insecure \
    --resolve "example.com:443:$PUBLIC_EDGE_ADDRESS" \
    'https://example.com/maintenance.html')
assert_status 404 "$status" maintenance_direct

status=$(request static_success \
    --insecure \
    --resolve "example.com:443:$PUBLIC_EDGE_ADDRESS" \
    'https://example.com/app.css')
assert_status 200 "$status" static_success
assert_header static_success 'Cache-Control: max-age=2592000'

status=$(request static_missing \
    --insecure \
    --resolve "example.com:443:$PUBLIC_EDGE_ADDRESS" \
    'https://example.com/missing.css')
assert_status 404 "$status" static_missing

status=$(request robots_missing \
    --insecure \
    --resolve "example.com:443:$PUBLIC_EDGE_ADDRESS" \
    'https://example.com/robots.txt')
assert_status 404 "$status" robots_missing

stop_nginx

grep --fixed-strings '"request_path":"/missing.css"' \
    "$STATIC_FAILURE_LOG" >/dev/null \
    || fail 'missing static asset was not recorded'
grep --fixed-strings '"request_path":"/robots.txt"' \
    "$STATIC_FAILURE_LOG" >/dev/null \
    || fail 'missing robots policy was not recorded'
if grep --fixed-strings '"request_path":"/app.css"' \
    "$STATIC_FAILURE_LOG" >/dev/null; then
    fail 'successful static asset was written to the failure-only log'
fi

printf '%s\n' 'nginx runtime policy checks passed'
