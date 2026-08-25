#!/bin/sh

set -eu

PROGRAM_NAME=${0##*/}
SCRIPT_DIRECTORY=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
REPOSITORY_ROOT=$(CDPATH='' cd -- "$SCRIPT_DIRECTORY/.." && pwd)
FAIL2BAN_INSTALLER=$REPOSITORY_ROOT/fail2ban/install
TEST_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/config-fail2ban-installer.XXXXXX")

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

assert_contains() {
    checked_file=$1
    expected_text=$2

    grep -F -- "$expected_text" "$checked_file" >/dev/null \
        || fail "$checked_file is missing: $expected_text"
}

assert_excludes() {
    checked_file=$1
    rejected_text=$2

    if grep -F -- "$rejected_text" "$checked_file" >/dev/null; then
        fail "$checked_file unexpectedly contains: $rejected_text"
    fi
}

trap cleanup EXIT HUP INT TERM

[ -x "$FAIL2BAN_INSTALLER" ] || fail 'fail2ban/install is not executable'
"$FAIL2BAN_INSTALLER" --check >/dev/null

cat > "$TEST_ROOT/rhel-9" <<'EOF'
ID=rhel
PRETTY_NAME="Red Hat Enterprise Linux 9.4 (Plow)"
VERSION_ID="9.4"
EOF

fake_binary_directory=$TEST_ROOT/fake-bin
mkdir "$fake_binary_directory"
cat > "$fake_binary_directory/dnf" <<'EOF'
#!/bin/sh

if [ "$#" -eq 3 ] \
    && [ "$1" = -q ] \
    && [ "$2" = repolist ] \
    && [ "$3" = --all ]; then
    cat "$FAKE_DNF_REPOSITORY_LIST"
    exit 0
fi

printf 'unexpected fake dnf invocation: %s\n' "$*" >&2
exit 1
EOF
cat > "$fake_binary_directory/uname" <<'EOF'
#!/bin/sh

[ "$#" -eq 1 ] && [ "$1" = -m ] || exit 1
printf 'x86_64\n'
EOF
chmod 0755 "$fake_binary_directory/dnf" "$fake_binary_directory/uname"

cat > "$TEST_ROOT/azure-eus.repos" <<'EOF'
repo id                                                  repo name status
codeready-builder-for-rhel-9-x86_64-eus-rhui-rpms        CodeReady enabled
rhel-9-for-x86_64-baseos-eus-rhui-rpms                   BaseOS enabled
rhel-9-for-x86_64-appstream-eus-rhui-rpms                AppStream enabled
EOF
expect_failure \
    'this installer does not support rolling EPEL with a pinned RHEL extended-update stream' \
    env \
    "FAKE_DNF_REPOSITORY_LIST=$TEST_ROOT/azure-eus.repos" \
    "PATH=$fake_binary_directory:$PATH" \
    "$FAIL2BAN_INSTALLER" \
    --plan \
    --os-release "$TEST_ROOT/rhel-9"
assert_contains "$TEST_ROOT/command.stderr" \
    'codeready-builder-for-rhel-9-x86_64-eus-rhui-rpms'
assert_contains "$TEST_ROOT/command.stderr" \
    'non-EUS RHUI on Azure'
assert_excludes "$TEST_ROOT/command.stdout" \
    'dnf install --assumeyes ca-certificates'

cat > "$TEST_ROOT/azure-current.repos" <<'EOF'
repo id                                                  repo name status
codeready-builder-for-rhel-9-x86_64-eus-rhui-rpms        CodeReady disabled
rhel-9-for-x86_64-baseos-eus-rhui-rpms                   BaseOS disabled
codeready-builder-for-rhel-9-x86_64-rhui-rpms            CodeReady enabled
rhel-9-for-x86_64-baseos-rhui-rpms                       BaseOS enabled
EOF
if ! FAKE_DNF_REPOSITORY_LIST="$TEST_ROOT/azure-current.repos" \
    PATH="$fake_binary_directory:$PATH" \
        "$FAIL2BAN_INSTALLER" \
        --plan \
        --os-release "$TEST_ROOT/rhel-9" \
        > "$TEST_ROOT/azure-current.plan"
then
    fail 'standard Azure RHUI installation plan failed'
fi
assert_contains "$TEST_ROOT/azure-current.plan" \
    'CodeReady Builder repository already enabled: codeready-builder-for-rhel-9-x86_64-rhui-rpms'
assert_contains "$TEST_ROOT/azure-current.plan" \
    'fail2ban fail2ban-firewalld fail2ban-selinux python3'

printf 'Validated Fail2ban installer EPEL stream boundaries.\n'
