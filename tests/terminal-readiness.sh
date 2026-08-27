#!/bin/sh

set -eu

PROGRAM_NAME=${0##*/}
SCRIPT_DIRECTORY=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
REPOSITORY_ROOT=$(CDPATH='' cd -- "$SCRIPT_DIRECTORY/.." && pwd)
TERMINAL_INSTALLER=$REPOSITORY_ROOT/terminal/install
TERMINAL_VERIFIER=$REPOSITORY_ROOT/terminal/verify
TEST_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/config-terminal-tests.XXXXXX")

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

    if "$@" > "$TEST_ROOT/command.stdout" 2> "$TEST_ROOT/command.stderr"; then
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

write_os_release() {
    release_path=$1
    release_id=$2
    release_name=$3
    release_version=$4

    printf 'ID=%s\nPRETTY_NAME="%s"\nVERSION_ID="%s"\n' \
        "$release_id" "$release_name" "$release_version" \
        > "$release_path"
}

[ -x "$TERMINAL_INSTALLER" ] || fail 'terminal/install is not executable'
[ -x "$TERMINAL_VERIFIER" ] || fail 'terminal/verify is not executable'
"$TERMINAL_INSTALLER" --help >/dev/null
"$TERMINAL_INSTALLER" --check >/dev/null
"$TERMINAL_VERIFIER" --help >/dev/null
"$TERMINAL_VERIFIER" --check >/dev/null

"$TERMINAL_VERIFIER" > "$TEST_ROOT/verify.stdout"
assert_contains "$TEST_ROOT/verify.stdout" \
    'tic and infocmp round-tripped xterm-256color'

write_os_release "$TEST_ROOT/rocky-9" rocky 'Rocky Linux 9.8' 9.8
write_os_release "$TEST_ROOT/rhel-10" rhel 'Red Hat Enterprise Linux 10.0' 10.0
write_os_release "$TEST_ROOT/centos-stream-10" centos 'CentOS Stream 10' 10
write_os_release "$TEST_ROOT/rocky-8" rocky 'Rocky Linux 8.10' 8.10
write_os_release "$TEST_ROOT/alma-9" almalinux 'AlmaLinux 9.6' 9.6
write_os_release "$TEST_ROOT/centos-linux-9" centos 'CentOS Linux 9' 9

for supported_release in rocky-9 rhel-10 centos-stream-10; do
    "$TERMINAL_INSTALLER" \
        --plan \
        --os-release "$TEST_ROOT/$supported_release" \
        > "$TEST_ROOT/$supported_release.plan"
    assert_contains "$TEST_ROOT/$supported_release.plan" \
        'dnf install --assumeyes ncurses ncurses-base'
    assert_contains "$TEST_ROOT/$supported_release.plan" \
        '/usr/local/bin/verify-terminal-readiness'
done

expect_failure \
    'unsupported OS version' \
    "$TERMINAL_INSTALLER" --plan --os-release "$TEST_ROOT/rocky-8"
expect_failure \
    'unsupported base OS: almalinux' \
    "$TERMINAL_INSTALLER" --plan --os-release "$TEST_ROOT/alma-9"
expect_failure \
    'CentOS Stream is required' \
    "$TERMINAL_INSTALLER" --plan --os-release "$TEST_ROOT/centos-linux-9"
expect_failure \
    '--os-release is accepted only with --plan' \
    "$TERMINAL_INSTALLER" --check --os-release "$TEST_ROOT/rocky-9"

mkdir "$TEST_ROOT/missing-entry-bin"
cat > "$TEST_ROOT/missing-entry-bin/infocmp" <<'EOF'
#!/bin/sh

exit 1
EOF
chmod 0755 "$TEST_ROOT/missing-entry-bin/infocmp"
expect_failure \
    'terminfo entry is unavailable: xterm-256color' \
    env "PATH=$TEST_ROOT/missing-entry-bin:$PATH" "$TERMINAL_VERIFIER"

mkdir "$TEST_ROOT/broken-compiler-bin"
cat > "$TEST_ROOT/broken-compiler-bin/tic" <<'EOF'
#!/bin/sh

exit 1
EOF
chmod 0755 "$TEST_ROOT/broken-compiler-bin/tic"
expect_failure \
    'tic could not compile the xterm-256color definition' \
    env "PATH=$TEST_ROOT/broken-compiler-bin:$PATH" "$TERMINAL_VERIFIER"

fixture_root=$TEST_ROOT/repository
mkdir -p "$fixture_root/deploy/lib" "$fixture_root/terminal"
cp "$REPOSITORY_ROOT/deploy/lib/platform.sh" "$fixture_root/deploy/lib/platform.sh"
cp "$TERMINAL_INSTALLER" "$fixture_root/terminal/install"
ln -s /etc/passwd "$fixture_root/terminal/verify"
expect_failure \
    'dependency must be a regular file' \
    "$fixture_root/terminal/install" --check

printf 'Validated terminal package routing and functional terminfo readiness.\n'
