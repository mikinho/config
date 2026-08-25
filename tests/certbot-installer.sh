#!/bin/sh

set -eu

PROGRAM_NAME=${0##*/}
SCRIPT_DIRECTORY=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
REPOSITORY_ROOT=$(CDPATH='' cd -- "$SCRIPT_DIRECTORY/.." && pwd)
CERTBOT_INSTALLER=$REPOSITORY_ROOT/certbot/install
TEST_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/config-certbot-installer.XXXXXX")

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

write_os_release() {
    release_path=$1
    release_id=$2
    release_name=$3
    release_version=$4

    printf 'ID=%s\nPRETTY_NAME="%s"\nVERSION_ID="%s"\n' \
        "$release_id" \
        "$release_name" \
        "$release_version" \
        > "$release_path"
}

assert_plan_contains() {
    plan_file=$1
    expected_line=$2
    grep -F -- "$expected_line" "$plan_file" >/dev/null \
        || fail "installation plan is missing: $expected_line"
}

assert_plan_excludes() {
    plan_file=$1
    rejected_line=$2
    if grep -F -- "$rejected_line" "$plan_file" >/dev/null; then
        fail "installation plan unexpectedly contains: $rejected_line"
    fi
}

trap cleanup EXIT HUP INT TERM

[ -x "$CERTBOT_INSTALLER" ] || fail 'certbot/install is not executable'
"$CERTBOT_INSTALLER" --help >/dev/null
"$CERTBOT_INSTALLER" --check >/dev/null

write_os_release "$TEST_ROOT/rhel-9" rhel 'Red Hat Enterprise Linux 9.7' 9.7
write_os_release "$TEST_ROOT/rhel-10" rhel 'Red Hat Enterprise Linux 10.3' 10.3
write_os_release "$TEST_ROOT/rocky-9" rocky 'Rocky Linux 9.7 (Blue Onyx)' 9.7
write_os_release "$TEST_ROOT/rocky-10" rocky 'Rocky Linux 10.1 (Red Quartz)' 10.1
write_os_release "$TEST_ROOT/centos-stream-9" centos 'CentOS Stream 9' 9
write_os_release "$TEST_ROOT/centos-stream-10" centos 'CentOS Stream 10 (Coughlan)' 10

for supported_release in \
    rhel-9 \
    rhel-10 \
    rocky-9 \
    rocky-10 \
    centos-stream-9 \
    centos-stream-10
do
    "$CERTBOT_INSTALLER" \
        --plan \
        --os-release "$TEST_ROOT/$supported_release" \
        > "$TEST_ROOT/$supported_release.plan"
    assert_plan_contains \
        "$TEST_ROOT/$supported_release.plan" \
        'dnf install --assumeyes certbot'
    assert_plan_contains \
        "$TEST_ROOT/$supported_release.plan" \
        'systemctl enable --now certbot.timer'
    assert_plan_contains \
        "$TEST_ROOT/$supported_release.plan" \
        'Plan complete; no host changes were made.'
done

assert_plan_contains \
    "$TEST_ROOT/rhel-9.plan" \
    'subscription-manager repos --enable codeready-builder-for-rhel-9-'
assert_plan_contains \
    "$TEST_ROOT/rhel-10.plan" \
    'epel-release-latest-10.noarch.rpm'
assert_plan_contains \
    "$TEST_ROOT/rocky-9.plan" \
    'dnf config-manager --set-enabled crb'
assert_plan_contains \
    "$TEST_ROOT/centos-stream-10.plan" \
    'Certbot installation target: CentOS Stream 10'

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
chmod 0755 "$fake_binary_directory/dnf"
cat > "$fake_binary_directory/uname" <<'EOF'
#!/bin/sh

[ "$#" -eq 1 ] && [ "$1" = -m ] || exit 1
printf 'x86_64\n'
EOF
chmod 0755 "$fake_binary_directory/uname"

cat > "$TEST_ROOT/azure-rhui-enabled.repos" <<'EOF'
repo id                                                  repo name status
codeready-builder-for-rhel-9-x86_64-rhui-rpms            CodeReady enabled
rhel-9-for-x86_64-baseos-rhui-rpms                       BaseOS enabled
EOF
FAKE_DNF_REPOSITORY_LIST="$TEST_ROOT/azure-rhui-enabled.repos" \
PATH="$fake_binary_directory:$PATH" \
    "$CERTBOT_INSTALLER" \
    --plan \
    --os-release "$TEST_ROOT/rhel-9" \
    > "$TEST_ROOT/azure-rhui-enabled.plan"
assert_plan_contains \
    "$TEST_ROOT/azure-rhui-enabled.plan" \
    'CodeReady Builder repository already enabled: codeready-builder-for-rhel-9-x86_64-rhui-rpms'
assert_plan_excludes \
    "$TEST_ROOT/azure-rhui-enabled.plan" \
    'subscription-manager repos --enable'

cat > "$TEST_ROOT/azure-rhui-disabled.repos" <<'EOF'
repo id                                                  repo name status
codeready-builder-for-rhel-9-x86_64-rhui-rpms            CodeReady disabled
rhel-9-for-x86_64-baseos-rhui-rpms                       BaseOS enabled
EOF
FAKE_DNF_REPOSITORY_LIST="$TEST_ROOT/azure-rhui-disabled.repos" \
PATH="$fake_binary_directory:$PATH" \
    "$CERTBOT_INSTALLER" \
    --plan \
    --os-release "$TEST_ROOT/rhel-9" \
    > "$TEST_ROOT/azure-rhui-disabled.plan"
assert_plan_contains \
    "$TEST_ROOT/azure-rhui-disabled.plan" \
    'dnf config-manager --set-enabled codeready-builder-for-rhel-9-x86_64-rhui-rpms'
assert_plan_excludes \
    "$TEST_ROOT/azure-rhui-disabled.plan" \
    'subscription-manager repos --enable'

cat > "$TEST_ROOT/aws-rhui-disabled.repos" <<'EOF'
repo id                                    repo name status
codeready-builder-for-rhel-9-rhui-rpms     CodeReady disabled
rhel-9-baseos-rhui-rpms                    BaseOS enabled
EOF
FAKE_DNF_REPOSITORY_LIST="$TEST_ROOT/aws-rhui-disabled.repos" \
PATH="$fake_binary_directory:$PATH" \
    "$CERTBOT_INSTALLER" \
    --plan \
    --os-release "$TEST_ROOT/rhel-9" \
    > "$TEST_ROOT/aws-rhui-disabled.plan"
assert_plan_contains \
    "$TEST_ROOT/aws-rhui-disabled.plan" \
    'dnf config-manager --set-enabled codeready-builder-for-rhel-9-rhui-rpms'
assert_plan_excludes \
    "$TEST_ROOT/aws-rhui-disabled.plan" \
    'subscription-manager repos --enable'

cat > "$TEST_ROOT/azure-rhui-missing-builder.repos" <<'EOF'
repo id                                    repo name status
rhel-9-for-x86_64-baseos-rhui-rpms         BaseOS enabled
rhel-9-for-x86_64-appstream-rhui-rpms      AppStream enabled
EOF
expect_failure \
    'CodeReady Builder repository is unavailable through configured RHEL RHUI repositories' \
    env \
    "FAKE_DNF_REPOSITORY_LIST=$TEST_ROOT/azure-rhui-missing-builder.repos" \
    "PATH=$fake_binary_directory:$PATH" \
    "$CERTBOT_INSTALLER" \
    --plan \
    --os-release "$TEST_ROOT/rhel-9"

cat > "$TEST_ROOT/azure-eus-enabled.repos" <<'EOF'
repo id                                                  repo name status
codeready-builder-for-rhel-9-x86_64-eus-rhui-rpms        CodeReady enabled
rhel-9-for-x86_64-baseos-eus-rhui-rpms                   BaseOS enabled
rhel-9-for-x86_64-appstream-eus-rhui-rpms                AppStream enabled
EOF
expect_failure \
    'this installer does not support rolling EPEL with a pinned RHEL extended-update stream' \
    env \
    "FAKE_DNF_REPOSITORY_LIST=$TEST_ROOT/azure-eus-enabled.repos" \
    "PATH=$fake_binary_directory:$PATH" \
    "$CERTBOT_INSTALLER" \
    --plan \
    --os-release "$TEST_ROOT/rhel-9"

cat > "$TEST_ROOT/subscription-e4s-enabled.repos" <<'EOF'
repo id                                             repo name status
codeready-builder-for-rhel-9-x86_64-e4s-rpms        CodeReady enabled
rhel-9-for-x86_64-baseos-e4s-rpms                   BaseOS enabled
EOF
expect_failure \
    'this installer does not support rolling EPEL with a pinned RHEL extended-update stream' \
    env \
    "FAKE_DNF_REPOSITORY_LIST=$TEST_ROOT/subscription-e4s-enabled.repos" \
    "PATH=$fake_binary_directory:$PATH" \
    "$CERTBOT_INSTALLER" \
    --plan \
    --os-release "$TEST_ROOT/rhel-9"

"$CERTBOT_INSTALLER" \
    --plan \
    --backend snap \
    --os-release "$TEST_ROOT/centos-stream-10" \
    > "$TEST_ROOT/centos-stream-10-snap.plan"
assert_plan_contains \
    "$TEST_ROOT/centos-stream-10-snap.plan" \
    'dnf install --assumeyes snapd'
assert_plan_contains \
    "$TEST_ROOT/centos-stream-10-snap.plan" \
    'snap install --classic certbot'
assert_plan_contains \
    "$TEST_ROOT/centos-stream-10-snap.plan" \
    'snap.certbot.renew.service.d/10-nginx.conf'
assert_plan_contains \
    "$TEST_ROOT/centos-stream-10-snap.plan" \
    'snap stop --disable certbot.renew'

write_os_release "$TEST_ROOT/almalinux-9" almalinux 'AlmaLinux 9.7' 9.7
write_os_release "$TEST_ROOT/centos-linux-9" centos 'CentOS Linux 9' 9
write_os_release "$TEST_ROOT/rocky-8" rocky 'Rocky Linux 8.10' 8.10

expect_failure \
    'unsupported base OS: almalinux' \
    "$CERTBOT_INSTALLER" \
    --plan \
    --os-release "$TEST_ROOT/almalinux-9"
expect_failure \
    'CentOS Stream is required' \
    "$CERTBOT_INSTALLER" \
    --plan \
    --os-release "$TEST_ROOT/centos-linux-9"
expect_failure \
    'unsupported OS version' \
    "$CERTBOT_INSTALLER" \
    --plan \
    --os-release "$TEST_ROOT/rocky-8"
expect_failure \
    'unsupported Certbot backend: arbitrary' \
    "$CERTBOT_INSTALLER" \
    --plan \
    --backend arbitrary \
    --os-release "$TEST_ROOT/rocky-9"
expect_failure \
    '--os-release is accepted only with --plan' \
    "$CERTBOT_INSTALLER" \
    --check \
    --os-release "$TEST_ROOT/rocky-9"

mkdir -p "$TEST_ROOT/etc" "$TEST_ROOT/usr/lib"
cp "$TEST_ROOT/rocky-9" "$TEST_ROOT/usr/lib/os-release"
ln -s ../usr/lib/os-release "$TEST_ROOT/etc/os-release"
"$CERTBOT_INSTALLER" \
    --plan \
    --os-release "$TEST_ROOT/etc/os-release" \
    > "$TEST_ROOT/symlink-release.plan"
assert_plan_contains \
    "$TEST_ROOT/symlink-release.plan" \
    'Certbot installation target: Rocky Linux 9'

ln -s missing-release "$TEST_ROOT/broken-release"
expect_failure \
    'OS release metadata must be a regular file' \
    "$CERTBOT_INSTALLER" \
    --plan \
    --os-release "$TEST_ROOT/broken-release"

mkdir "$TEST_ROOT/release-directory"
ln -s release-directory "$TEST_ROOT/directory-release"
expect_failure \
    'OS release metadata must be a regular file' \
    "$CERTBOT_INSTALLER" \
    --plan \
    --os-release "$TEST_ROOT/directory-release"

fixture_root=$TEST_ROOT/repository
mkdir -p "$fixture_root"
cp -R \
    "$REPOSITORY_ROOT/certbot" \
    "$REPOSITORY_ROOT/deploy" \
    "$REPOSITORY_ROOT/systemd" \
    "$fixture_root/"
rm "$fixture_root/systemd/system/certbot.timer"
ln -s /etc/passwd "$fixture_root/systemd/system/certbot.timer"
expect_failure \
    'repository source must be a regular file' \
    "$fixture_root/certbot/install" \
    --check

rm "$fixture_root/systemd/system/certbot.timer"
cp "$REPOSITORY_ROOT/systemd/system/certbot.timer" \
    "$fixture_root/systemd/system/certbot.timer"
rm "$fixture_root/deploy/lib/platform.sh"
ln -s /etc/passwd "$fixture_root/deploy/lib/platform.sh"
expect_failure \
    'platform library must be a regular file' \
    "$fixture_root/certbot/install" \
    --check

printf 'Validated Certbot installer OS routing and backend plans.\n'
