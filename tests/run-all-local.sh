#!/bin/sh

set -eu

PROGRAM_NAME=${0##*/}
SCRIPT_DIRECTORY=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
REPOSITORY_ROOT=$(CDPATH='' cd -- "$SCRIPT_DIRECTORY/.." && pwd)
SCRATCH_DIR=$(mktemp -d "${TMPDIR:-/tmp}/config-local-tests.XXXXXX")

cleanup() {
    if [ -d "$SCRATCH_DIR" ]; then
        rm -rf -- "$SCRATCH_DIR"
    fi
}

trap cleanup EXIT HUP INT TERM

fail() {
    printf '%s: [FAIL] %s\n' "$PROGRAM_NAME" "$*" >&2
    exit 1
}

step() {
    printf '==> %s\n' "$*"
}

step "Running ShellCheck on shell scripts..."
if command -v shellcheck >/dev/null 2>&1; then
    shellcheck \
        "$REPOSITORY_ROOT/deploy/install-nginx" \
        "$REPOSITORY_ROOT/deploy/install-php-site" \
        "$REPOSITORY_ROOT/deploy/install-host-tools" \
        "$REPOSITORY_ROOT/deploy/verify-deployment" \
        "$REPOSITORY_ROOT/deploy/certbot-healthcheck" \
        "$REPOSITORY_ROOT/deploy/lib/platform.sh" \
        "$REPOSITORY_ROOT/deploy/setup-host" \
        "$REPOSITORY_ROOT/certbot/install" \
        "$REPOSITORY_ROOT/certbot/setup" \
        "$REPOSITORY_ROOT/fail2ban/install" \
        "$REPOSITORY_ROOT/fail2ban/setup" \
        "$REPOSITORY_ROOT/firewalld/install" \
        "$REPOSITORY_ROOT/firewalld/setup" \
        "$REPOSITORY_ROOT/nginx/setup" \
        "$REPOSITORY_ROOT/packages/rsync/build-el9" \
        "$REPOSITORY_ROOT/selinux/apply-nginx-policy" \
        "$REPOSITORY_ROOT/selinux/install" \
        "$REPOSITORY_ROOT/selinux/setup" \
        "$REPOSITORY_ROOT/ssh/install" \
        "$REPOSITORY_ROOT/ssh/setup" \
        "$REPOSITORY_ROOT/tests/certbot-installer.sh" \
        "$REPOSITORY_ROOT/tests/deploy-renderers.sh" \
        "$REPOSITORY_ROOT/tests/health-verifiers.sh" \
        "$REPOSITORY_ROOT/tests/host-setup.sh" \
        "$REPOSITORY_ROOT/tests/nginx-runtime.sh" \
        "$REPOSITORY_ROOT/tests/rsync-packaging.sh" \
        "$REPOSITORY_ROOT/tests/run-all-local.sh"
    printf 'ShellCheck passed with 0 warnings.\n'
else
    printf 'Notice: shellcheck not found in PATH; skipping static shell analysis.\n'
fi

step "Validating deployment profile assignments..."
"$REPOSITORY_ROOT/deploy/install-nginx" --check

step "Validating gold application deployment profiles..."
python3 "$REPOSITORY_ROOT/tests/application-deployment-standard.py"
python3 "$REPOSITORY_ROOT/deploy/application/validate_profile.py" \
    "$REPOSITORY_ROOT/deploy/application/profiles/example_node_app.json" >/dev/null

step "Exercising gold application deployment transactions..."
python3 "$REPOSITORY_ROOT/tests/application-deployment-transactions.py"

step "Running deployment renderer and path boundary tests..."
"$REPOSITORY_ROOT/tests/deploy-renderers.sh"

step "Running Certbot installer routing tests..."
"$REPOSITORY_ROOT/tests/certbot-installer.sh"

step "Running component and host setup tests..."
"$REPOSITORY_ROOT/tests/host-setup.sh"

step "Running strict host and certificate healthcheck tests..."
"$REPOSITORY_ROOT/tests/health-verifiers.sh"

step "Running rsync packaging contract tests..."
"$REPOSITORY_ROOT/tests/rsync-packaging.sh"

step "Exercising full profile render..."
"$REPOSITORY_ROOT/deploy/install-nginx" \
    --output "$SCRATCH_DIR/nginx-full" \
    --profile brotli \
    --profile gzip \
    --profile post-quantum \
    --profile quic-bpf \
    --profile trusted-proxy \
    --profile websocket \
    --profile wordpress-cache >/dev/null

[ -s "$SCRATCH_DIR/nginx-full/quic_host.key" ] \
    || fail "QUIC host key was not generated in full render"
[ -s "$SCRATCH_DIR/nginx-full/stubs/http/post-quantum.conf" ] \
    || fail "post-quantum stub was not installed in full render"

step "Exercising PHP site render..."
"$REPOSITORY_ROOT/deploy/install-php-site" \
    --output "$SCRATCH_DIR/php-site" \
    --tag example_wp >/dev/null

[ -f "$SCRATCH_DIR/php-site/etc/php-fpm.d/sites/example_wp.conf" ] \
    || fail "PHP site config missing from render"

printf '\nAll local pre-commit checks and tests passed successfully.\n'
