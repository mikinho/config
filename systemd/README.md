# systemd units

Sandboxed service units for nginx, per-site PHP-FPM, and Certbot renewal. The
design rationale and cross-component contracts are documented in the
[root README](../README.md); PHP-specific provisioning is documented in the
[PHP-FPM README](../php-fpm/README.md).

Installable units live under `systemd/system/` to mirror their destination
under `/etc/systemd/system/`; repository-only documentation remains here.

## Installation

Install the common nginx and optional PHP-FPM units first:

```sh
install -m 0644 systemd/system/nginx.service /etc/systemd/system/nginx.service
install -m 0644 systemd/system/php-fpm@.service /etc/systemd/system/php-fpm@.service
systemctl daemon-reload
systemctl enable --now nginx.service
```

On SELinux-enforcing hosts, run `selinux/apply-nginx-policy` before the first
start so the unit's runtime directories are created with the right labels and
the QUIC listener and worker rlimits are permitted. Mask or remove any
distribution-provided nginx unit before enabling this one.

Choose exactly one Certbot backend. Both use the repository's `certbot.timer`;
do not leave a distribution or Snap Certbot timer active alongside it.
When switching backends, stop `certbot.timer` before changing files. Reinstalling
the base timer does not remove anything under `certbot.timer.d/`.

### Native Certbot backend

Use this backend only when `/bin/certbot` is a native executable rather than a
Snap launcher:

```sh
install -m 0644 systemd/system/certbot.service /etc/systemd/system/certbot.service
install -m 0644 systemd/system/certbot.timer /etc/systemd/system/certbot.timer
systemctl daemon-reload
systemctl enable --now certbot.timer
```

The timer explicitly selects `certbot.service` as its oneshot payload. Do not
install the Snap drop-ins with this backend. When retiring the Snap backend,
remove or park both of these exact files before reloading systemd:

```text
/etc/systemd/system/certbot.timer.d/10-snap-runner.conf
/etc/systemd/system/snap.certbot.renew.service.d/10-nginx.conf
```

### Snap Certbot backend

Install the Certbot Snap first and confirm that snapd has generated
`snap.certbot.renew.service`. Never edit or copy that generated base unit: its
revision-specific mount dependencies and working directory are owned by
snapd. Install the repository timer and optional Snap drop-ins instead:

```sh
install -m 0644 systemd/system/certbot.timer /etc/systemd/system/certbot.timer
install -D -m 0644 \
    systemd/system/certbot.timer.d/10-snap-runner.conf \
    /etc/systemd/system/certbot.timer.d/10-snap-runner.conf
install -D -m 0644 \
    systemd/system/snap.certbot.renew.service.d/10-nginx.conf \
    /etc/systemd/system/snap.certbot.renew.service.d/10-nginx.conf
restorecon -RFv \
    /etc/systemd/system/certbot.timer.d \
    /etc/systemd/system/snap.certbot.renew.service.d
systemctl daemon-reload
snap stop --disable certbot.renew
systemctl enable --now certbot.timer
```

`snap stop --disable certbot.renew` disables Snap's automatic timer activation
for the renewal service; it does not uninstall or disable the Certbot CLI. The
repository timer can still start `snap.certbot.renew.service` explicitly. The
matching service drop-in restores the native backend's nginx ordering,
one-hour timeout, restrictive umask, partial-renewal reload, and visible
reload-failure behavior. Do not also install an executable Certbot deploy hook
that reloads nginx.

The native units locate their binaries at `/usr/sbin/nginx`, `/sbin/php-fpm`,
`/bin/certbot`, and `/bin/systemctl`. The Snap backend delegates execution to
the snapd-generated service instead of invoking the Snap launcher inside the
native Certbot sandbox. `/sbin/php-fpm` is deliberately an
administrator-managed, version-neutral contract so a Remi installation can
select its intended parallel PHP release without versioning the unit name.
Validate that symlink after package changes.

Provision and enable PHP-FPM instances individually; do not enable the example
`sample_wp` instance without first creating a real site-specific copy. There is
no `php-fpm@.socket`: PHP-FPM owns its socket and `pm = ondemand` starts workers
only when requests arrive.

## Local changes are drop-ins

Never edit the installed unit in place; use
`systemctl edit nginx.service` so upgrades stay clean. Expected cases:

- A site writing outside `/var/lib/nginx` needs the narrowest possible
  `ReadWritePaths=` addition.
- A PHP application gets no document-root writes from the generic template.
  Install an instance drop-in like the public `sample_wp` example with only
  the required `ReadWritePaths=` entries.
- An nginx build using PCRE JIT needs `MemoryDenyWriteExecute=no` after review;
  otherwise the JIT silently falls back and only costs regex performance.
- A PHP workload requiring PCRE, OPcache, or extension JIT needs the same
  systemd override plus a review of SELinux
  `httpd_execmem`; otherwise one containment layer still blocks executable
  writable mappings.
- For nginx, tightening `CapabilityBoundingSet=` or adding `SystemCallFilter=`
  is worthwhile but host-specific; validate against the installed kernel,
  selected stubs, and nginx build before deploying, and revalidate on upgrades.
- Native Certbot deployments using non-webroot authenticators or hooks that
  write elsewhere need the smallest necessary additions to
  `certbot.service`. Review Snap-specific changes against its generated
  service separately.

## Validation

```sh
sudo systemd-analyze verify /etc/systemd/system/nginx.service
# Run only the selected Certbot backend block.
# Native Certbot backend:
sudo systemd-analyze verify /etc/systemd/system/certbot.service /etc/systemd/system/certbot.timer
sudo systemd-analyze security nginx.service certbot.service
sudo systemctl start certbot.service
sudo systemctl --no-pager show certbot.service \
    -p Result -p ExecMainCode -p ExecMainStatus -p ExecStopPost
# Snap Certbot backend:
sudo systemd-analyze verify certbot.timer snap.certbot.renew.service
sudo systemctl start snap.certbot.renew.service
sudo systemctl --no-pager show snap.certbot.renew.service \
    -p Result -p ExecMainCode -p ExecMainStatus -p ExecStopPost -p DropInPaths
# Expected for Snap: repository timer enabled/active; Snap timer
# disabled/inactive.
sudo systemctl --no-pager show \
    certbot.timer snap.certbot.renew.timer \
    -p Id -p UnitFileState -p ActiveState
# Both backends:
sudo systemctl --no-pager show certbot.timer \
    -p Unit -p TimersCalendar -p RandomizedDelayUSec -p Persistent
sudo systemctl list-timers --no-pager | grep -Ei 'certbot|letsencrypt'
sudo systemd-analyze verify php-fpm@SITE_TAG.service
sudo systemd-analyze security nginx.service php-fpm@SITE_TAG.service
sudo certbot renew --dry-run
```

CI runs `systemd-analyze verify` for every push on Rocky Linux 9 and CentOS
Stream 10 and composes the optional Snap drop-ins against a representative
generated service. On a Snap host, repeat the single-scheduler and manual
service-start checks after Certbot Snap refreshes. `systemd-analyze security`
scores are advisory: this baseline deliberately keeps nginx's capabilities
unrestricted until validated per host, so read the report against the
documented rationale rather than chasing the number.
