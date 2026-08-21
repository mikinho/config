# Per-site PHP-FPM baseline

This directory provides a public-safe example for running one PHP-FPM master
per site under a matching systemd instance. It targets supported RHEL,
CentOS Stream, and Rocky Linux hosts. Other distributions are best-effort
ports.

## Compatibility and dependencies

| Component | Supported baseline | Requirement |
| --- | --- | --- |
| PHP-FPM | PHP 8.3 or newer | The binary must include systemd notification and POSIX ACL support. |
| systemd | 249 or newer | Required for the service sandbox used by `php-fpm@.service`. |
| SELinux | Enforcing with the RHEL `httpd` policy | Runtime, session, and application write paths must carry the correct labels. |
| nginx | The repository nginx baseline | nginx connects through `/run/$site_tag/php-fpm.sock`. |

Install PHP-FPM from the selected RHEL-family or Remi package stream, plus the
SELinux administration tools used by the deployment procedure. Use only the
command matching the selected packaging model:

```sh
# Native RHEL-family PHP stream
dnf install php-fpm policycoreutils policycoreutils-python-utils libselinux-utils

# Parallel Remi PHP 8.3 collection
dnf install php83-php-fpm policycoreutils policycoreutils-python-utils libselinux-utils
```

Parallel Remi packages may use a versioned executable under `/opt/remi`. This
repository deliberately keeps the unit name and command stable: the
administrator must make `/sbin/php-fpm` resolve to the selected binary. The
FPM configuration root remains `/etc/php-fpm.d`, while the binary continues to
load the `php.ini` and extension tree compiled into that selected package.

Validate that contract before installing an instance:

```sh
readlink -f /sbin/php-fpm
/sbin/php-fpm --version
/sbin/php-fpm --info | grep -E 'Configure Command|Configuration File|Scan this dir'
```

Treat the symlink as managed host configuration and revalidate it after PHP
package changes. Do not commit a host-specific target path to this public
repository. Public CI validates the native Rocky Linux and CentOS Stream
packages; a parallel Remi installation must additionally pass the host-side
validation below.

## Per-site contract

Replace every `sample_wp` token in the pool, main configuration, and writable-
path drop-in with a site tag composed of lowercase letters, digits, and
underscores. The same tag identifies:

- the system user and group;
- `php-fpm@SITE_TAG.service`;
- `/etc/php-fpm.d/sites/SITE_TAG.conf`;
- `/etc/php-fpm.d/pool/SITE_TAG.conf`;
- `/run/SITE_TAG/php-fpm.sock`;
- `/var/lib/php/session/SITE_TAG`; and
- the sample's `/var/www/SITE_TAG` content root.

The content-root convention is not mandatory. When an existing application
uses a different directory name, change the pool paths and its instance
drop-in together; the generic service template deliberately contains no
document-root assumption.

The service runs the entire FPM master as the site user. Pool-level `user` and
`group` directives are therefore intentionally absent. PHP-FPM, rather than a
systemd socket unit, creates the listening socket and grants nginx access with
`listen.acl_users`.

The example uses `pm = ondemand`, which starts workers when requests arrive;
the small supervised master remains running. Size `pm.max_children` from the
site's measured worker RSS and its assigned memory budget. The example value
is not a capacity recommendation.

## Provisioning an instance

Create the site identity and required application directories first. The
systemd unit creates the runtime and session directories itself.

```sh
useradd --system --user-group --no-create-home \
  --home-dir /var/www/SITE_TAG --shell /sbin/nologin SITE_TAG
install -d -o SITE_TAG -g SITE_TAG -m 0755 /var/www/SITE_TAG
install -d -o SITE_TAG -g SITE_TAG -m 0700 /var/www/SITE_TAG/var
install -d -o SITE_TAG -g SITE_TAG -m 0700 /var/www/SITE_TAG/var/tmp
```

For WordPress, also provision its writable uploads directory. Core, plugin,
and theme updates remain read-only by default and require a deliberate local
write-path extension or an out-of-band deployment process.

```sh
install -d -o SITE_TAG -g SITE_TAG -m 0755 \
  /var/www/SITE_TAG/wordpress/wp-content/uploads
```

Render the site-specific configuration set with the repository renderer,
which validates the tag against `^[a-z][a-z0-9_]*$`, refuses an existing
output, never touches the live system, and verifies that no `sample_wp`
token survives:

```sh
deploy/install-php-site --output /tmp/php-example_wp --tag example_wp
```

Review the rendered tree and its `INSTALL-SITE` manifest, then install it as
laid out — the paths under `etc/` mirror their destinations exactly. Do not
enable a corresponding `.socket` unit; PHP-FPM owns the socket.

```sh
site_tag=example_wp
install -d -m 0755 /etc/php-fpm.d/sites /etc/php-fpm.d/pool
install -m 0644 "/tmp/php-$site_tag/etc/php-fpm.d/sites/$site_tag.conf" \
  "/etc/php-fpm.d/sites/$site_tag.conf"
install -m 0644 "/tmp/php-$site_tag/etc/php-fpm.d/pool/$site_tag.conf" \
  "/etc/php-fpm.d/pool/$site_tag.conf"
install -D -m 0644 \
  "/tmp/php-$site_tag/etc/systemd/system/php-fpm@$site_tag.service.d/writable-paths.conf" \
  "/etc/systemd/system/php-fpm@$site_tag.service.d/writable-paths.conf"
install -m 0644 systemd/php-fpm@.service /etc/systemd/system/php-fpm@.service
systemctl daemon-reload
systemctl enable --now "php-fpm@$site_tag.service"
```

## SELinux

Keep SELinux enforcing. Register the per-site runtime path before the first
start, and label only the application directories PHP is allowed to modify:

```sh
semanage fcontext --add --type httpd_var_run_t "/run/SITE_TAG(/.*)?"
semanage fcontext --add --type httpd_sys_rw_content_t \
  "/var/www/SITE_TAG/var(/.*)?"
semanage fcontext --add --type httpd_sys_rw_content_t \
  "/var/www/SITE_TAG/wordpress/wp-content/uploads(/.*)?"
restorecon -RF /var/www/SITE_TAG
```

The distribution policy normally covers `/var/lib/php/session(/.*)?`. Confirm
that it also covers the per-site directory with:

```sh
matchpathcon /var/lib/php/session/SITE_TAG
```

Add a narrower persistent rule only if the installed policy does not return an
`httpd`-writable type. Investigate denials with `ausearch`; never disable
SELinux to make the service start.

## Security and application overrides

The generic systemd unit permits writes only to its managed runtime/session
paths and private temporary directories. The sample instance drop-in adds
`/var/www/SITE_TAG/var` and an existing WordPress uploads directory. Replace
those paths for a different layout, and add any other required location with
`systemctl edit php-fpm@SITE_TAG.service`; then rerun configuration and
application tests.

`SocketBindDeny=any` blocks network listeners while leaving outbound client
connections and the Unix-domain socket untouched; a pool mistakenly changed
to a TCP `listen` fails at startup instead of exposing a port. A candidate
further step is `PrivateUsers=yes`, reasonable because the master runs
unprivileged — but validate the socket ACL grant and mixed-ownership content
reads on a real site before adopting it, in the same deferred spirit as the
nginx capability set.

`MemoryDenyWriteExecute=yes` intentionally prevents PHP, PCRE, and extensions
from creating writable executable mappings. A workload that truly requires
JIT must set it to `no` in a local drop-in and review the corresponding SELinux
`httpd_execmem` permission. Relax both layers together only after measuring the
benefit and auditing the added execution surface.

The example's `open_basedir`, `allow_url_fopen`, and `disable_functions`
settings are additional application policy, not security boundaries. In
particular, `open_basedir` disables PHP's realpath cache, and some WordPress
plugins or deployment tools require functions in the disabled list. Adjust
the site pool after compatibility testing; do not weaken the systemd or SELinux
boundary merely to preserve those PHP settings.

The sample hard-stops a web request after 60 seconds and leaves nginx at its
default 60-second FastCGI read timeout. These controls have different semantics:
FPM limits total request lifetime, while nginx limits silence between upstream
reads. Neither slows a successful request. Raising both to 300 seconds would
increase the worst-case occupancy of each FPM worker fivefold, so use
[WP-CLI](https://developer.wordpress.org/cli/) or a background worker for
long-running updates, imports, backups, and maintenance.
If a site genuinely needs a longer browser-driven operation, raise both its
pool deadline and its custom nginx location timeout, then re-evaluate
`pm.max_children`, rate limits, and failure recovery together.

Master, worker, and application errors are routed to the per-instance journal;
the baseline creates no writable log file under the website. PHP-FPM slow logs
are deliberately opt-in because collecting a worker backtrace uses `ptrace`
and may require both a system-call-filter and SELinux exception. Likewise, do
not expose `pm.status_path` through a public virtual host. A deployment needing
status metrics should use a separate local-only status listener and authorize
only its monitoring agent.

## Validation and operation

Run the configuration test as the site user because the systemd service also
runs the master without root privileges:

```sh
sudo -u SITE_TAG /sbin/php-fpm \
  --test --fpm-config /etc/php-fpm.d/sites/SITE_TAG.conf
systemd-analyze verify php-fpm@SITE_TAG.service
systemd-analyze security php-fpm@SITE_TAG.service
systemctl status php-fpm@SITE_TAG.service
sudo -u nginx test -r /run/SITE_TAG/php-fpm.sock
sudo -u nginx test -w /run/SITE_TAG/php-fpm.sock
journalctl --unit php-fpm@SITE_TAG.service
```

Public CI performs the configuration test on Rocky Linux 9 and CentOS Stream
10, starts the sample master directly as `sample_wp`, verifies the live socket
ACL as `nginx`, and exercises graceful `SIGQUIT` shutdown. It does not emulate
a booted systemd manager or enforcing SELinux; the unit, labels, application
traffic, and complete sandbox still require the target-host checks above.

`systemd-analyze security` is advisory; verify the application, outbound
dependencies, uploads, sessions, reload, and graceful shutdown on the target
host. Reloading sends PHP-FPM `SIGUSR2`; stopping sends `SIGQUIT` for graceful
worker termination.
