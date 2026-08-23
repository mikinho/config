# SELinux assets

SELinux stays enforcing on every supported host; nothing in this baseline
requires permissive mode or a disabled policy. The distribution `httpd` policy
already covers most of what nginx needs. This directory supplies only the
pieces the base policy does not provide, plus the administration steps that
are deployment-specific.

Install the administration tooling first:

```sh
dnf install policycoreutils policycoreutils-python-utils libselinux-utils
```

After installing or overlaying configuration and content, restore labels
before starting services:

```sh
restorecon -RF /etc/nginx /var/lib/nginx /var/www
```

## The nginx policy script

`apply-nginx-policy` registers everything the nginx baseline needs beyond
the distribution policy. It is idempotent and safe to re-run after policy
updates:

| Registration | Why |
| --- | --- |
| `/var/run/nginx(/.*)?` and `/var/run/lock/nginx(/.*)?` as `httpd_var_run_t` | RHEL's fcontext equivalency maps these canonical specifications onto the `/run/nginx` and `/run/lock/nginx` runtime paths. The base policy labels only `/var/run/nginx.pid`, not the complete directories `systemd/system/nginx.service` creates. |
| `http_port_t` on UDP 443 | Port labels are per protocol and `http_port_t` historically lists TCP only; without this the QUIC listener cannot bind under enforcing mode. |
| `httpd_setrlimit` on | `worker_rlimit_nofile` needs setrlimit permission. Without it nginx starts normally and workers silently keep the default descriptor limit — a failure that only surfaces under load. |

```sh
sudo selinux/apply-nginx-policy
```

It deliberately registers nothing else: `/var/log/nginx`, `/var/lib/nginx`,
`/var/www`, and `/etc/nginx` are labeled correctly by the distribution
policy, and duplicate local rules would only obscure future policy updates.
The hardened SSH port has its own registration in `ssh/README.md`.

## Per-site application sockets

The by-tag contract places each site's application socket under
`/run/$site_tag/` — the PHP-FPM socket, or a Node.js socket proxied with the
shared includes. Those directories are deployment-specific, so register each
one where the site is provisioned:

```sh
sudo semanage fcontext --add --type httpd_var_run_t "/var/run/SITE_TAG(/.*)?"
sudo restorecon -RF /run/SITE_TAG
```

Replace `SITE_TAG` with the site's tag. The `/var/run` spelling is required
when registering the rule because RHEL maps `/run` through an SELinux
fcontext equivalency; the service and socket still use `/run/SITE_TAG`.
Without the rule, the socket is created as plain `var_run_t` and nginx's
connection to it is denied. Register
the rule before the application service first starts so its runtime directory
and socket are born with the right label. PHP-FPM owns its socket; this
baseline does not use a PHP systemd socket unit.

PHP sites also need writable application content labeled narrowly. The
per-site service permits its `var` directory and, when present, WordPress
uploads; label those locations without making the complete site tree writable:

```sh
sudo semanage fcontext --add --type httpd_sys_rw_content_t \
  "/var/www/SITE_TAG/var(/.*)?"
sudo semanage fcontext --add --type httpd_sys_rw_content_t \
  "/var/www/SITE_TAG/wordpress/wp-content/uploads(/.*)?"
sudo restorecon -RF /var/www/SITE_TAG
```

The RHEL-family policy normally covers `/var/lib/php/session(/.*)?` with an
`httpd`-writable type. Verify the per-site systemd state directory with
`matchpathcon /var/lib/php/session/SITE_TAG` rather than adding a duplicate
local rule. See `php-fpm/README.md` for the complete provisioning contract.

## Booleans

Enable `httpd_can_network_connect` only on hosts where nginx proxies to a
TCP upstream; Unix-domain sockets do not need it and are preferred when the
application runs on the same host:

```sh
sudo setsebool -P httpd_can_network_connect on
```

A tighter alternative for TCP upstreams: label the upstream port
`http_port_t` and enable only `httpd_can_network_relay`, which permits
connecting to web ports without opening general outbound network access.
Check first — many application ports carry an unrelated label that must be
modified rather than added:

```sh
sudo semanage port --modify --type http_port_t --proto tcp 3000
sudo setsebool -P httpd_can_network_relay on
```

Do not enable broader booleans preemptively. Add each one in response to an
audited denial for a workload the host actually runs. In particular,
`httpd_execmem` pairs with a build that uses PCRE JIT: the systemd unit's
`MemoryDenyWriteExecute=` already blocks JIT by default, and a reviewed
deployment that relaxes it must relax both controls together or the JIT
still fails.

## quic_bpf policy module

`nginx_quic_bpf.te` accompanies the `quic-bpf` deployment profile. The base
policy grants `httpd_t` no BPF permissions, so `quic_bpf on;` fails under
enforcing mode without it. Build and load the module only on hosts that
select that profile:

```sh
dnf install checkpolicy
checkmodule -M -m -o nginx_quic_bpf.mod selinux/nginx_quic_bpf.te
semodule_package -o nginx_quic_bpf.pp -m nginx_quic_bpf.mod
sudo semodule -i nginx_quic_bpf.pp
```

Remove it with `semodule -r nginx_quic_bpf` when the profile is removed. The
module targets the CAP_BPF capability path used by RHEL 9 era kernels; if the
target kernel or nginx build still produces a denial, extend the module only
with the specific audited permission. CI builds the module on Rocky Linux 9
and CentOS Stream 10 so a syntax error cannot reach a host.

## Investigating denials

Reproduce the failure, then read the audit trail before changing anything:

```sh
sudo ausearch -m avc -ts recent
sudo ausearch -m avc -ts recent | audit2allow
```

Treat `audit2allow` output as a diagnosis, not a patch: prefer the correct
file context or an existing boolean over a custom allow rule, and never widen
policy for a denial that indicates misbehavior. `matchpathcon PATH` shows the
label a path should carry, and `semanage port -l | grep http_port_t` shows
the effective port labels. Remember that some failures are silent — the
setrlimit case above never logs at startup unless auditing is watched — so
verify effective behavior, not just the absence of errors.
