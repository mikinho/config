%global _hardened_build 1

Summary: A fast, versatile remote and local file-copying tool
Name: rsync
Version: 3.5.0
# Sort below a future vendor 3.5.0-1 package while still upgrading EL9's
# 3.2.5 package family.
Release: 0.1.mikinho%{?dist}
URL: https://rsync.samba.org/
License: GPL-3.0-or-later
Vendor: Mikinho, LLC

Source0: https://download.samba.org/pub/rsync/src/rsync-%{version}.tar.gz
Source1: https://download.samba.org/pub/rsync/src/rsync-%{version}.tar.gz.asc
Source2: rsync-%{version}-signing-key.asc
Source3: rsyncd.conf

BuildRequires: acl
BuildRequires: attr
BuildRequires: binutils
BuildRequires: gcc
BuildRequires: gcc-c++
BuildRequires: gawk
BuildRequires: gnupg2
BuildRequires: libacl-devel
BuildRequires: libattr-devel
BuildRequires: lz4-devel
BuildRequires: make
BuildRequires: openssl-devel
BuildRequires: popt-devel
BuildRequires: python3
BuildRequires: libzstd-devel
BuildRequires: zlib-devel

%description
Rsync synchronizes files locally or across a network using a delta-transfer
algorithm. This EL9 security rebuild follows Fedora's 3.5.0 configuration and
runs the complete upstream test suite during package construction.

%package rrsync
Summary: Restricted rsync wrapper for SSH-only transfer accounts
BuildArch: noarch
Requires: %{name} = %{version}-%{release}
Requires: python3

%description rrsync
The rrsync wrapper constrains an SSH key to a reviewed rsync directory and
option set. Version 3.5.0 adds the confinement required for restricted
deployment identities.

%prep
verification_home="$(mktemp -d)"
chmod 0700 "$verification_home"
trap 'rm -rf "$verification_home"' EXIT HUP INT TERM
gpg --batch --homedir "$verification_home" --import "%{SOURCE2}"
signing_fingerprint="$(
  gpg --batch --homedir "$verification_home" --with-colons --fingerprint |
    awk -F: '$1 == "fpr" { print $10; exit }'
)"
test "$signing_fingerprint" = \
  9FEF112DCE19A0DC7E882CB81BB24997A8535F6F
gpg --batch --homedir "$verification_home" \
  --verify "%{SOURCE1}" "%{SOURCE0}"
rm -rf "$verification_home"
trap - EXIT HUP INT TERM

%autosetup -n rsync-%{version}

# The signed release includes convenience copies of popt and zlib. Remove
# their implementation files before configure so this EL9 package cannot
# silently compile either dependency instead of the patched system library.
find popt zlib -type f ! -name dummy.in -delete

%build
%configure \
  --enable-openssl \
  --disable-xxhash \
  --enable-zstd \
  --enable-lz4 \
  --enable-ipv6 \
  --enable-acl-support \
  --enable-xattr-support \
  --with-included-popt=no \
  --with-included-zlib=no \
  --with-rrsync

# Release tarballs contain generated manuals. Keep make from attempting to
# regenerate them with a Markdown module that is not available on EL9.
touch rsync.1 rsync-ssl.1 rrsync.1 rsyncd.conf.5

grep -Fx '#define EXTERNAL_ZLIB 1' config.h
%{make_build}

%check
make check CHECK_J=%{_smp_build_ncpus}
./rsync --version | grep -F 'rsync  version 3.5.0'
python3 -m py_compile support/rrsync
for library in libz.so libpopt.so libcrypto.so libzstd.so liblz4.so; do
  readelf -d ./rsync | grep -F "$library"
done

%install
%{make_install} INSTALLCMD='install -p' INSTALLMAN='install -p'
install -D -m 0644 %{SOURCE3} %{buildroot}%{_sysconfdir}/rsyncd.conf
sed -i '1s|^#!/usr/bin/env python3$|#!/usr/bin/python3|' \
  %{buildroot}%{_bindir}/rrsync
grep -Fx '#!/usr/bin/python3' %{buildroot}%{_bindir}/rrsync

%files
%license COPYING
%doc NEWS.md README.md SECURITY.md
%{_bindir}/rsync
%{_bindir}/rsync-ssl
%{_mandir}/man1/rsync.1*
%{_mandir}/man1/rsync-ssl.1*
%{_mandir}/man5/rsyncd.conf.5*
%config(noreplace) %{_sysconfdir}/rsyncd.conf

%files rrsync
%license COPYING
%{_bindir}/rrsync
%{_mandir}/man1/rrsync.1*

%changelog
* Sun Aug 23 2026 Michael Welter <me@mikinho.com> - 3.5.0-0.1.mikinho
- Build the upstream 3.5.0 security release for CentOS Stream 9.
- Package the hardened Python rrsync wrapper separately.
