# systemd units

Sandboxed service units for nginx, per-site PHP-FPM, and Certbot renewal. The
design rationale and cross-component contracts are documented in the
[root README](../README.md); PHP-specific provisioning is documented in the
[PHP-FPM README](../php-fpm/README.md).

## Installation

```sh
install -m 0644 systemd/nginx.service /etc/systemd/system/nginx.service
install -m 0644 systemd/certbot.service /etc/systemd/system/certbot.service
install -m 0644 systemd/certbot.timer /etc/systemd/system/certbot.timer
install -m 0644 systemd/php-fpm@.service /etc/systemd/system/php-fpm@.service
systemctl daemon-reload
systemctl enable --now nginx.service certbot.timer
```

Enable the timer, not `certbot.service` directly; the service is its oneshot
payload. On SELinux-enforcing hosts, run `selinux/apply-nginx-policy` before
the first start so the unit's runtime directories are created with the right
labels and the QUIC listener and worker rlimits are permitted. Mask or remove any
distribution-provided nginx unit and Certbot renewal scheduler first — two
active renewal schedulers is a misconfiguration.

The units locate their binaries at `/usr/sbin/nginx`, `/sbin/php-fpm`,
`/bin/certbot`, and `/bin/systemctl`. `/sbin/php-fpm` is deliberately an
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
- Certbot deployments using non-webroot authenticators or hooks that write
  elsewhere need the smallest necessary additions to `certbot.service`.

## Validation

```sh
sudo systemd-analyze verify /etc/systemd/system/nginx.service
sudo systemd-analyze verify /etc/systemd/system/certbot.service /etc/systemd/system/certbot.timer
sudo systemd-analyze verify php-fpm@SITE_TAG.service
sudo systemd-analyze security nginx.service certbot.service php-fpm@SITE_TAG.service
sudo certbot renew --dry-run
systemctl list-timers certbot.timer
```

CI runs `systemd-analyze verify` for every push on Rocky Linux 9 and CentOS
Stream 10. `systemd-analyze security` scores are advisory: this baseline
deliberately keeps nginx's capabilities unrestricted until validated per
host, so read the report against the documented rationale rather than
chasing the number.
