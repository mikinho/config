# systemd units

Sandboxed service units for nginx and Certbot renewal. The design rationale —
what the sandboxes allow, why the capability bounding set and system-call
filter are deferred, and the renewal reload behavior — is documented in the
[root README](../README.md).

## Installation

```sh
install -m 0644 systemd/nginx.service /etc/systemd/system/nginx.service
install -m 0644 systemd/certbot.service /etc/systemd/system/certbot.service
install -m 0644 systemd/certbot.timer /etc/systemd/system/certbot.timer
systemctl daemon-reload
systemctl enable --now nginx.service certbot.timer
```

Enable the timer, not `certbot.service` directly; the service is its oneshot
payload. On SELinux-enforcing hosts, run `selinux/apply-nginx-file-contexts`
before the first start so the unit's `/run/nginx` and `/run/lock/nginx`
directories are created with the right labels. Mask or remove any
distribution-provided nginx unit and Certbot renewal scheduler first — two
active renewal schedulers is a misconfiguration.

The units locate their binaries at `/usr/sbin/nginx`, `/bin/certbot`, and
`/bin/systemctl`. Adjust with a drop-in if a package installs elsewhere.

## Local changes are drop-ins

Never edit the installed unit in place; use
`systemctl edit nginx.service` so upgrades stay clean. Expected cases:

- A site writing outside `/var/lib/nginx` needs the narrowest possible
  `ReadWritePaths=` addition.
- A build with PCRE JIT or another JIT-based module needs
  `MemoryDenyWriteExecute=no` after review; otherwise the JIT silently falls
  back and only costs regex performance.
- Tightening `CapabilityBoundingSet=` or adding `SystemCallFilter=` is
  worthwhile but host-specific; validate against the installed kernel,
  selected stubs, and nginx build before deploying, and revalidate on
  upgrades.
- Certbot deployments using non-webroot authenticators or hooks that write
  elsewhere need the smallest necessary additions to `certbot.service`.

## Validation

```sh
sudo systemd-analyze verify /etc/systemd/system/nginx.service
sudo systemd-analyze verify /etc/systemd/system/certbot.service /etc/systemd/system/certbot.timer
sudo systemd-analyze security nginx.service certbot.service
sudo certbot renew --dry-run
systemctl list-timers certbot.timer
```

CI runs `systemd-analyze verify` for every push on Rocky Linux 9 and CentOS
Stream 10. `systemd-analyze security` scores are advisory: this baseline
deliberately keeps nginx's capabilities unrestricted until validated per
host, so read the report against the documented rationale rather than
chasing the number.
