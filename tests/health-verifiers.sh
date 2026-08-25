#!/bin/sh
# Mock scripts below intentionally preserve runtime shell expansions.
# shellcheck disable=SC2016

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

expect_failure 'unknown topology: arbitrary' \
    "$HOST_VERIFIER" --topology arbitrary
expect_failure 'invalid firewalld zone' \
    "$HOST_VERIFIER" --zone '../public'
expect_failure 'unknown SSH phase: arbitrary' \
    "$HOST_VERIFIER" --ssh-phase arbitrary
expect_failure 'invalid expected administrative CIDR' \
    "$HOST_VERIFIER" --ignore-ip 'not a CIDR'
expect_failure 'invalid expected administrative CIDR' \
    "$HOST_VERIFIER" --ignore-ip '999.0.0.1/33'
expect_failure 'expected administrative address must use CIDR notation' \
    "$HOST_VERIFIER" --ignore-ip '192.0.2.1'
expect_failure 'invalid PHP-FPM site tag' \
    "$HOST_VERIFIER" --site '../site'

mkdir -p "$TEST_ROOT/clock-bin"
printf '%s\n' \
    '#!/bin/sh' \
    '[ "${MOCK_CLOCK_STATE:-yes}" = yes ] && printf "yes\\n" || printf "no\\n"' \
    > "$TEST_ROOT/clock-bin/timedatectl"
printf '%s\n' \
    '#!/bin/sh' \
    '[ "${MOCK_CHRONY_NORMAL:-no}" = yes ] && printf "Leap status     : Normal\\n" || printf "Leap status     : Not synchronised\\n"' \
    > "$TEST_ROOT/clock-bin/chronyc"
chmod 0755 "$TEST_ROOT/clock-bin/timedatectl" "$TEST_ROOT/clock-bin/chronyc"
clock_function=$(extract_function check_clock_synchronization "$HOST_VERIFIER")
for clock_contract in timedatectl chrony; do
    case "$clock_contract" in
        timedatectl) clock_environment='MOCK_CLOCK_STATE=yes MOCK_CHRONY_NORMAL=no' ;;
        chrony) clock_environment='MOCK_CLOCK_STATE=no MOCK_CHRONY_NORMAL=yes' ;;
    esac
    clock_result=$(PATH="$TEST_ROOT/clock-bin:$PATH" \
        sh -c "$clock_environment
export MOCK_CLOCK_STATE MOCK_CHRONY_NORMAL
FAILED_CHECKS=0
fail_msg() { FAILED_CHECKS=\$((FAILED_CHECKS + 1)); }
pass_msg() { :; }
$clock_function
check_clock_synchronization >/dev/null
printf '%s\\n' \"\$FAILED_CHECKS\"
")
    [ "$clock_result" = 0 ] \
        || fail "$clock_contract clock verification recorded $clock_result failures"
done
clock_failure_result=$(PATH="$TEST_ROOT/clock-bin:$PATH" \
    MOCK_CLOCK_STATE=no MOCK_CHRONY_NORMAL=no sh -c "
FAILED_CHECKS=0
fail_msg() { FAILED_CHECKS=\$((FAILED_CHECKS + 1)); }
pass_msg() { :; }
$clock_function
check_clock_synchronization >/dev/null
printf '%s\\n' \"\$FAILED_CHECKS\"
")
[ "$clock_failure_result" = 1 ] \
    || fail "unsynchronized clock recorded $clock_failure_result failures"

mkdir -p "$TEST_ROOT/ssh-bin"
printf '%s\n' \
    '#!/bin/sh' \
    'case "$1" in' \
    '    -t) exit 0 ;;' \
    '    -T)' \
    '        [ "${MOCK_SSH_PHASE:-final}" = prepare ] && printf "port 22\\n"' \
    '        printf "%s\\n" "port 2356" "permitrootlogin no" "passwordauthentication no" "kbdinteractiveauthentication no"' \
    '        ;;' \
    '    *) exit 1 ;;' \
    'esac' \
    > "$TEST_ROOT/ssh-bin/sshd"
chmod 0755 "$TEST_ROOT/ssh-bin/sshd"
ssh_function=$(extract_function check_ssh_hardening "$HOST_VERIFIER")
for tested_ssh_phase in prepare final; do
    ssh_result=$(PATH="$TEST_ROOT/ssh-bin:$PATH" \
        MOCK_SSH_PHASE="$tested_ssh_phase" sh -c "
FAILED_CHECKS=0
SSH_PHASE=$tested_ssh_phase
fail_msg() { FAILED_CHECKS=\$((FAILED_CHECKS + 1)); }
pass_msg() { :; }
$ssh_function
check_ssh_hardening >/dev/null
printf '%s\\n' \"\$FAILED_CHECKS\"
")
    [ "$ssh_result" = 0 ] \
        || fail "$tested_ssh_phase SSH verification recorded $ssh_result failures"
done

mkdir -p "$TEST_ROOT/firewall-bin"
printf '%s\n' \
    '#!/bin/sh' \
    'case "$*" in' \
    '    --state) exit 0 ;;' \
    '    --get-zones) printf "public edge-custom\\n" ;;' \
    '    --info-service=nginx | --info-service=ssh-hardened) exit 0 ;;' \
    '    "--zone=edge-custom --list-services")' \
    '        printf "nginx ssh-hardened"' \
    '        [ "${MOCK_FIREWALL_SSH:-final}" = prepare ] && printf " ssh"' \
    '        printf "\\n"' \
    '        ;;' \
    '    *) exit 1 ;;' \
    'esac' \
    > "$TEST_ROOT/firewall-bin/firewall-cmd"
chmod 0755 "$TEST_ROOT/firewall-bin/firewall-cmd"
firewall_function=$(extract_function check_firewalld "$HOST_VERIFIER")
for tested_firewall_phase in prepare final; do
    firewall_result=$(PATH="$TEST_ROOT/firewall-bin:$PATH" \
        MOCK_FIREWALL_SSH="$tested_firewall_phase" sh -c "
FAILED_CHECKS=0
ZONE=edge-custom
SSH_PHASE=$tested_firewall_phase
fail_msg() { FAILED_CHECKS=\$((FAILED_CHECKS + 1)); }
pass_msg() { :; }
$firewall_function
check_firewalld >/dev/null
printf '%s\\n' \"\$FAILED_CHECKS\"
")
    [ "$firewall_result" = 0 ] \
        || fail "$tested_firewall_phase firewalld verification recorded $firewall_result failures"
done

mkdir -p "$TEST_ROOT/fail2ban-bin"
printf '%s\n' \
    '#!/bin/sh' \
    'case "$1" in' \
    '    is-active | is-enabled) exit 0 ;;' \
    '    *) exit 1 ;;' \
    'esac' \
    > "$TEST_ROOT/fail2ban-bin/systemctl"
printf '%s\n' \
    '#!/bin/sh' \
    'case "$1" in' \
    '    -t) exit 0 ;;' \
    '    status)' \
    '        [ "$2" = sshd ] && exit 0' \
    '        [ "${MOCK_TOPOLOGY:-direct}" = direct ] && exit 0' \
    '        exit 1' \
    '        ;;' \
    '    *) exit 1 ;;' \
    'esac' \
    > "$TEST_ROOT/fail2ban-bin/fail2ban-client"
chmod 0755 \
    "$TEST_ROOT/fail2ban-bin/systemctl" \
    "$TEST_ROOT/fail2ban-bin/fail2ban-client"
printf '%s\n' \
    '[DEFAULT]' \
    'ignoreip = 127.0.0.1/8 ::1 192.0.2.0/24' \
    '' \
    '[sshd]' \
    'enabled = true' \
    'port = 2356' \
    > "$TEST_ROOT/fail2ban-policy.local"
fail2ban_status_function=$(extract_function fail2ban_jail_active "$HOST_VERIFIER")
fail2ban_check_function=$(extract_function check_fail2ban "$HOST_VERIFIER")
for tested_topology in direct proxied; do
    fail2ban_result=$(PATH="$TEST_ROOT/fail2ban-bin:$PATH" \
        MOCK_TOPOLOGY="$tested_topology" sh -c "
FAILED_CHECKS=0
FAIL2BAN_POLICY=$TEST_ROOT/fail2ban-policy.local
TOPOLOGY=$tested_topology
SSH_PHASE=final
EXPECTED_IGNORE_IPS=192.0.2.0/24
fail_msg() { FAILED_CHECKS=\$((FAILED_CHECKS + 1)); }
pass_msg() { :; }
$fail2ban_status_function
$fail2ban_check_function
check_fail2ban >/dev/null
printf '%s\n' \"\$FAILED_CHECKS\"
")
    [ "$fail2ban_result" = 0 ] \
        || fail "$tested_topology Fail2ban verification recorded $fail2ban_result failures"
done

missing_ignore_result=$(PATH="$TEST_ROOT/fail2ban-bin:$PATH" \
    MOCK_TOPOLOGY=direct sh -c "
FAILED_CHECKS=0
FAIL2BAN_POLICY=$TEST_ROOT/fail2ban-policy.local
TOPOLOGY=direct
SSH_PHASE=final
EXPECTED_IGNORE_IPS=198.51.100.0/24
fail_msg() { FAILED_CHECKS=\$((FAILED_CHECKS + 1)); }
pass_msg() { :; }
$fail2ban_status_function
$fail2ban_check_function
check_fail2ban >/dev/null
printf '%s\n' \"\$FAILED_CHECKS\"
")
[ "$missing_ignore_result" = 1 ] \
    || fail "missing Fail2ban ignore CIDR recorded $missing_ignore_result failures"

certificate_function=$(extract_function check_certificate_health "$HOST_VERIFIER")
printf '#!/bin/sh\nprintf "certificate ok\\n"\n' \
    > "$TEST_ROOT/certificate-pass"
printf '#!/bin/sh\nprintf "certificate failed\\n" >&2\nexit 1\n' \
    > "$TEST_ROOT/certificate-fail"
chmod 0755 "$TEST_ROOT/certificate-pass" "$TEST_ROOT/certificate-fail"
certificate_result=$(sh -c "
FAILED_CHECKS=0
VERBOSE=0
CERTBOT_HEALTHCHECK=$TEST_ROOT/certificate-pass
fail_msg() { FAILED_CHECKS=\$((FAILED_CHECKS + 1)); }
pass_msg() { :; }
info_msg() { :; }
$certificate_function
check_certificate_health >/dev/null
CERTBOT_HEALTHCHECK=$TEST_ROOT/certificate-fail
check_certificate_health >/dev/null
printf '%s\n' \"\$FAILED_CHECKS\"
")
[ "$certificate_result" = 1 ] \
    || fail "certificate health verification recorded $certificate_result failures"

for nginx_verifier in \
    "$REPOSITORY_ROOT/deploy/install-nginx" \
    "$HOST_VERIFIER"
do
    nginx_parser=$(extract_function parse_nginx_version "$nginx_verifier")
    stock_version=$(sh -c "
$nginx_parser
parse_nginx_version 'nginx version: nginx/1.29.3'
")
    getpagespeed_version=$(sh -c "
$nginx_parser
parse_nginx_version 'nginx version: nginx-mod by GetPageSpeed.com/1.30.4'
")
    invalid_version=$(sh -c "
$nginx_parser
parse_nginx_version 'nginx version: nginx-mod by GetPageSpeed.com/not-a-version'
")
    unrelated_version=$(sh -c "
$nginx_parser
parse_nginx_version 'not an nginx banner/1.30.4'
")

    [ "$stock_version" = 1.29.3 ] \
        || fail "stock nginx version was parsed as: $stock_version"
    [ "$getpagespeed_version" = 1.30.4 ] \
        || fail "GetPageSpeed nginx version was parsed as: $getpagespeed_version"
    [ -z "$invalid_version" ] \
        || fail "invalid nginx version was accepted as: $invalid_version"
    [ -z "$unrelated_version" ] \
        || fail "unrelated nginx banner was accepted as: $unrelated_version"
done

printf 'Validated strict host and certificate healthcheck behavior.\n'
