#!/bin/sh

set -eu

SCRIPT_DIRECTORY=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
REPOSITORY_ROOT=$(CDPATH='' cd -- "$SCRIPT_DIRECTORY/.." && pwd)
BUILDER=$REPOSITORY_ROOT/packages/rsync/build-el9
SPEC=$REPOSITORY_ROOT/packages/rsync/rsync.spec

fail() {
    printf 'rsync-packaging: %s\n' "$*" >&2
    exit 1
}

[ -x "$BUILDER" ] || fail "builder is not executable: $BUILDER"
[ -f "$SPEC" ] && [ ! -L "$SPEC" ] || fail "spec is not a real file: $SPEC"

"$BUILDER" --check

grep -Fqx 'Version: 3.5.0' "$SPEC"
grep -Fqx 'Release: 0.1.mikinho%{?dist}' "$SPEC"
grep -Fq -- '--with-rrsync' "$SPEC"
grep -Fq -- '--with-included-popt=no' "$SPEC"
grep -Fq -- '--with-included-zlib=no' "$SPEC"
grep -Fqx "grep -Fx '#define EXTERNAL_ZLIB 1' config.h" "$SPEC"
grep -Fqx 'make check CHECK_J=%{_smp_build_ncpus}' "$SPEC"
grep -Fqx '%{_bindir}/rrsync' "$SPEC"
grep -Fqx '%config(noreplace) %{_sysconfdir}/rsyncd.conf' "$SPEC"

if "$BUILDER" --check --output /tmp/rsync-invalid-output; then
    fail 'builder accepted multiple modes'
fi

printf 'Validated rsync packaging assets.\n'
