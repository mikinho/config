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

## File contexts

`apply-nginx-file-contexts` registers persistent local rules for the two
runtime directories created by `systemd/nginx.service`, which the base policy
does not label:

| Path | Context |
| --- | --- |
| `/run/nginx(/.*)?` | `httpd_var_run_t` |
| `/run/lock/nginx(/.*)?` | `httpd_var_run_t` |

The script is idempotent and relabels the directories when they already
exist. It deliberately adds nothing else: `/var/log/nginx`, `/var/lib/nginx`,
`/var/www`, and `/etc/nginx` are labeled correctly by the distribution
policy, and duplicate local rules would only obscure future policy updates.

```sh
sudo selinux/apply-nginx-file-contexts
```

## Per-site PHP-FPM sockets

The PHP-FPM contract places each site's socket at `/run/$site_tag/php-fpm.sock`.
Those directories are deployment-specific, so register each one where the
site is provisioned:

```sh
sudo semanage fcontext --add --type httpd_var_run_t "/run/SITE_TAG(/.*)?"
sudo restorecon -RF /run/SITE_TAG
```

Replace `SITE_TAG` with the site's tag. Without the rule, the socket is
created as plain `var_run_t` and nginx's connection to it is denied.

## Booleans

Enable `httpd_can_network_connect` only on hosts where nginx proxies to a TCP
upstream; Unix-domain sockets do not need it and are preferred when the
application runs on the same host:

```sh
sudo setsebool -P httpd_can_network_connect on
```

Do not enable broader booleans preemptively. Add each one in response to an
audited denial for a workload the host actually runs.

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
with the specific audited permission.

## Investigating denials

Reproduce the failure, then read the audit trail before changing anything:

```sh
sudo ausearch -m avc -ts recent
sudo ausearch -m avc -ts recent | audit2allow
```

Treat `audit2allow` output as a diagnosis, not a patch: prefer the correct
file context or an existing boolean over a custom allow rule, and never widen
policy for a denial that indicates misbehavior. `matchpathcon PATH` shows the
label a path should carry. Non-standard content roots need a persistent
`semanage fcontext` rule followed by `restorecon`, exactly as the root README
describes.
