# rsync 3.5.0 EL9 security rebuild

CentOS Stream 9 currently supplies rsync 3.2.5. That release cannot provide
the `--confine-root` and `--drop-D` interface used by a hardened `rrsync`
restricted account. This directory produces a temporary EL9 package pair from
the signed upstream 3.5.0 security release:

- `rsync-3.5.0-0.1.mikinho.el9.x86_64.rpm`
- `rsync-rrsync-3.5.0-0.1.mikinho.el9.noarch.rpm`

The `0.1` release intentionally sorts below a future vendor `3.5.0-1` build,
so a normal distribution package can replace this bridge without an epoch or
manual downgrade.

## Trust and audit contract

`build-el9` downloads the source archive, detached signature, and exact
release-signing key over HTTPS. Before executing any source it verifies:

- pinned SHA-256 digests for all three inputs;
- the signing key's full fingerprint,
  `9FEF112DCE19A0DC7E882CB81BB24997A8535F6F`; and
- the detached upstream signature over the source archive.

The SRPM repeats the fingerprint and detached-signature checks during `%prep`,
removes the bundled popt and zlib implementations, and asserts that the build
uses EL9's patched system libraries. The RPM build runs the complete upstream
`make check` suite. It then extracts the resulting packages and checks rsync's
version and confinement options, rrsync's inherited-lock mode, and the
security-contract markers used by the Vulcan deploy boundary. The published
output includes the source, signature, signing key, source RPM, debuginfo,
spec, build log, dependency NEVRAs, builder image identity, metadata, and a
`SHA256SUMS` manifest.

Only the `rsync` and `rsync-rrsync` packages are installed; the automatic
debuginfo/debugsource packages are retained as audit artifacts. The binary
RPMs are local and unsigned. That is acceptable only for a reviewed test-host
bridge when the complete output directory is transferred together and
`SHA256SUMS` is verified immediately before installation. Production use
requires signing and publishing the packages through the organization's RPM
repository. Never replace this process with an untracked `make install` into
`/usr`.

## Build on CentOS Stream 9

Install the build prerequisites:

```bash
sudo dnf install -y \
  acl attr binutils cpio curl gcc gcc-c++ gawk gnupg2 \
  libacl-devel libattr-devel libzstd-devel lz4-devel make \
  openssl-devel popt-devel python3 rpm-build zlib-devel
```

Create a parent directory and build as an unprivileged account:

```bash
mkdir -p /home/michael/rsync-build

./packages/rsync/build-el9 \
  --output /home/michael/rsync-build/rsync-3.5.0-el9
```

The builder refuses UID 0. The output parent must belong to the build user and
must not be group- or world-writable. The output directory must not already
exist. Build failure publishes nothing.

## Verify and install on the test host

```bash
cd /home/michael/rsync-build/rsync-3.5.0-el9
sha256sum --check --strict SHA256SUMS

if rpm -q rsync-daemon; then
  echo 'Stop: rsync-daemon requires a matching package build.' >&2
  exit 1
fi

rpm --checksig --nosignature \
  rsync-3.5.0-0.1.mikinho.el9.x86_64.rpm \
  rsync-rrsync-3.5.0-0.1.mikinho.el9.noarch.rpm

sudo dnf install -y --nogpgcheck \
  ./rsync-3.5.0-0.1.mikinho.el9.x86_64.rpm \
  ./rsync-rrsync-3.5.0-0.1.mikinho.el9.noarch.rpm
```

`--nogpgcheck` applies only to these exact local RPMs after verification of the
complete signed-source build evidence. It must not be used with a repository
or an independently transferred package.

Validate the installed interface:

```bash
/usr/bin/rsync --version | sed -n '1p'
/usr/bin/rsync --help |
  grep -E -- '--confine-root=DIR|--drop-D|--fsync'
/usr/bin/rrsync -help 2>&1 |
  grep -- '-no-lock'
```

Keep the build output as the host's package-provenance evidence until a signed
vendor or organizational package supersedes it.

The packaged `rsyncd.conf` is byte-for-byte identical to CentOS Stream 9's
[configuration at the reviewed dist-git commit](https://gitlab.com/redhat/centos-stream/rpms/rsync/-/blob/15f6d55f9a45f220973836eef8a43c8090c67fc2/rsyncd.conf).
