#!/bin/sh
# Interactive Bash is intentional in the behavioral checks below.
# shellcheck disable=SC2016

set -eu

PROGRAM_NAME=${0##*/}
SCRIPT_DIRECTORY=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
REPOSITORY_ROOT=$(CDPATH='' cd -- "$SCRIPT_DIRECTORY/.." && pwd)
TEST_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/config-shell-history.XXXXXX")
SHELL_SETUP=$REPOSITORY_ROOT/shell/setup
POLICY_SOURCE=$REPOSITORY_ROOT/shell/profile.d/90-no-persistent-history.sh

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

[ -x "$SHELL_SETUP" ] || fail "shell setup is not executable"
[ -f "$POLICY_SOURCE" ] && [ ! -L "$POLICY_SOURCE" ] \
    || fail "shell policy source is not a regular file"

"$SHELL_SETUP" --help >/dev/null
"$SHELL_SETUP" --check >/dev/null
"$SHELL_SETUP" --plan > "$TEST_ROOT/setup.plan"
grep -F -- '/etc/profile.d/90-no-persistent-history.sh' \
    "$TEST_ROOT/setup.plan" >/dev/null \
    || fail "shell setup plan omitted the managed target"
"$SHELL_SETUP" --plan --replace /etc/profile.d/00-legacy-history.sh \
    > "$TEST_ROOT/replacement.plan"
grep -F -- 'rm -f -- /etc/profile.d/00-legacy-history.sh' \
    "$TEST_ROOT/replacement.plan" >/dev/null \
    || fail "shell setup replacement plan omitted the legacy target"
expect_failure \
    'replacement must name one .sh file directly under /etc/profile.d' \
    "$SHELL_SETUP" --plan --replace /tmp/legacy-history.sh
expect_failure \
    'replacement must name one .sh file directly under /etc/profile.d' \
    "$SHELL_SETUP" --plan --replace /etc/profile.d/nested/legacy-history.sh
expect_failure \
    'replacement policy must differ from the managed target' \
    "$SHELL_SETUP" --plan \
    --replace /etc/profile.d/90-no-persistent-history.sh

bash --noprofile --norc -c '
    HISTFILE=/tmp/noninteractive-history
    HISTSIZE=41
    . "$1"
    [ "$HISTFILE" = /tmp/noninteractive-history ]
    [ "$HISTSIZE" = 41 ]
' sh "$POLICY_SOURCE" \
    || fail "policy changed non-interactive Bash state"

mkdir -p "$TEST_ROOT/home"
HOME=$TEST_ROOT/home bash --noprofile --norc -ic '
    HISTFILE=$HOME/.bash_history
    HISTSIZE=41
    . "$1"
    [ "$HISTFILE" = /dev/null ]
    [ "$HISTSIZE" = 41 ]
    readonly -p HISTFILE >/dev/null 2>&1
    history -s "config-shell-history-test"
    history -w
' sh "$POLICY_SOURCE" >/dev/null 2>&1 \
    || fail "policy did not retain in-session history without persistence"
[ ! -e "$TEST_ROOT/home/.bash_history" ] \
    || fail "interactive policy created a persistent Bash history file"

fixture_root=$TEST_ROOT/repository
mkdir -p "$fixture_root/shell/profile.d"
cp "$SHELL_SETUP" "$fixture_root/shell/setup"
chmod 0755 "$fixture_root/shell/setup"
ln -s /etc/passwd \
    "$fixture_root/shell/profile.d/90-no-persistent-history.sh"
expect_failure \
    'repository source must be a regular file' \
    "$fixture_root/shell/setup" --check

printf 'Validated non-persistent Bash history source and setup boundaries.\n'
