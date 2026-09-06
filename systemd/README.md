# systemd units

Sandboxed service units for nginx, per-site PHP-FPM, and Certbot renewal. The
design rationale and cross-component contracts are documented in the
[root README](../README.md); PHP-specific provisioning is documented in the
[PHP-FPM README](../php-fpm/README.md).

Installable units live under `systemd/system/` to mirror their destination
under `/etc/systemd/system/`; repository-only documentation remains here.

## Runtime directory modes

Choose `RuntimeDirectoryMode=` from the operations each identity needs; do not
copy a mode without its ownership and access model. A service that creates a
PID file, socket, or temporary file in its runtime directory needs owner write
and search permission, so a read-only owner mode such as `0550` is invalid.

| Mode | Use |
| --- | --- |
| `0700` | Only the service accesses the runtime directory. |
| `0710` | A group peer reaches a known pathname, such as a Unix socket, but must not enumerate the directory. |
| `0750` | Group members intentionally inspect or enumerate the runtime directory. |
| `0755` | Other users intentionally need discovery or traversal; require an explicit rationale. |

For a service-owned Unix socket shared with one peer group, prefer `0710` for
the directory and grant the required access on the socket itself, commonly
`0660`. The service owner retains `rwx`; the peer group receives search-only
access to the known pathname; all other users are denied. Group membership or
a directory ACL, the socket mode or ACL, and any SELinux/AppArmor policy must
all authorize the same peer. Directory mode alone is not an access contract.

Validate the composed behavior on the target service after every change:

1. Restart the service so systemd recreates the runtime directory.
2. Confirm the directory owner, group, and exact mode with `stat`.
3. Confirm the peer can connect to the known socket.
4. Confirm the peer cannot list the directory when using `0710`.
5. Exercise restart and reload paths plus the public or local health check.
6. Confirm the mandatory-access-control domain and labels remain correct.

`UMask=` supplies a process's initial file-creation mask. It does not change
existing files, override an application's explicit `chmod` or socket ACL, or
stop the process changing its own mask. Directory modes are independent of the
mask. Verify the running process and newly created output, including after
reload and rotation; checking `systemctl show -p UMask` alone is insufficient.

| Service | Mask and composed access contract |
| --- | --- |
| nginx | `0027`; foreground root master, primary group `nginx`; nginx workers; root-owned logs directory `root:nginx 0750`, new logs `0640`. |
| PHP-FPM | `0022` deliberately keeps public uploads readable by nginx. Private application and session directories remain `0700`. Runtime `0711` permits traversal to the known socket, whose `0660` mode and nginx ACL control connection access. |
| Certbot | `0077` for secret-bearing operations. The webroot authenticator explicitly creates publicly readable HTTP-01 tokens; those tokens are not private keys. |
| Certificate healthcheck | `0077` by default; optional report output is explicitly `0644` monitoring metadata. Choose its parent directory's access accordingly. |

Do not globally tighten the PHP mask or runtime directory without supplying the
matching nginx group or ACL access. nginx runtime and state parents retain
`0755` for conventional PID discovery and worker traversal into nginx-owned
temporary/cache subdirectories; private payloads are protected at those child
paths. The log directory additionally denies discovery and traversal to other
users. Review any local ACLs as part of that directory contract.

## Installation

For nginx, prefer `nginx/setup` or the composed `deploy/setup-host` command.
Both require reviewed worker, thread-pool, and task-budget inputs before apply
and explicitly **restart nginx**, even when it is already active. Schedule an
interruption: `daemon-reload` updates systemd's configuration, while nginx's HUP
reload retains its existing master and cannot activate new process sandboxing.
Config-only deployments and certificate renewal still use the preflighted
`ExecReload` path; they do not need a service restart.

Review the assembled `nginx -T` output on the host. For `worker_processes auto`,
`getconf _NPROCESSORS_ONLN` is a starting point for the CPU count; confirm what
the installed nginx build will use. Sum the thread counts of **all** pools
initialized per worker, including the implicit `default` pool (32 threads)
when selected by `aio threads`. Do not assume a custom configuration has one
pool. Pass the reviewed values as `--workers` and `--threads-per-worker` to
`nginx/setup`, or `--nginx-workers` and `--nginx-threads-per-worker` to
`deploy/setup-host`.

The third input, `--tasks-budget` (or `--nginx-tasks-budget`), is the operator's
reviewed available task capacity after accounting for ancestor cgroup limits
and other services sharing those limits. It does not change a limit. Setup
uses the smallest of that assertion, the shared `TasksMax=512`, and the loaded
unit's `TasksMax` when present. Stale manager metadata and unknown loaded limits
fail closed. The same check runs before mutation and after `daemon-reload`,
before restart. Read-only planning does not certify the live host's capacity.
If `LoadState=not-found` before the first unit installation, the reviewed
operator budget and shared ceiling govern that initial check; the installed
unit is checked again after `daemon-reload`. A masked, broken, or otherwise
unknown unit state is rejected. The supported nginx binary and reviewed
configuration must already be installed; this is not an nginx package installer.

The sizing check requires `2 × workers × (1 + threads-per-worker) + 16` tasks:
two worker generations plus a 16-task reserve for the master, helpers, and
short-lived service commands. Four workers with one 32-thread pool require
280 tasks; eight require 544 and fail the shared 512 ceiling. This is headroom
for **one** overlapping reload, not a bound on repeated reloads while old
workers are still draining. Wait for the prior generation to exit; increase
the reviewed reserve/budget through a separately validated design if the host
needs more. Do not remove the task cap to make preflight pass. A larger
host-specific limit is a separate reviewed change; these shared setup commands
deliberately retain their conservative 512 ceiling.

Use `nginx/setup --capacity-check --workers 4 --threads-per-worker 32
--tasks-budget 512` only after confirming those illustrative values match the
selected configuration and host. Inputs are canonical decimal integers up to
32767; workers and task budget must be positive, and zero pool threads is valid
only when the selected nginx configuration initializes no thread pools.

The manual equivalent also needs the same capacity and host-policy review.
Install the common nginx and optional PHP-FPM units, then perform the planned
restart rather than assuming `enable --now` replaces an active master:

```sh
install -D -o root -g root -m 0755 nginx/verify-runtime /usr/local/libexec/nginx-runtime-verify
restorecon /usr/local/libexec/nginx-runtime-verify
install -m 0644 systemd/system/nginx.service /etc/systemd/system/nginx.service
install -m 0644 systemd/system/php-fpm@.service /etc/systemd/system/php-fpm@.service
# One-time migration: change only the managed directory parents. systemd can
# recursively chown descendants if a managed parent's ownership differs.
# Preserve existing nginx-owned cache entries and rotated-log ownership.
for nginx_directory in /run/nginx /run/lock/nginx /var/lib/nginx /var/log/nginx; do
    if [ -e "$nginx_directory" ] || [ -L "$nginx_directory" ]; then
        [ -d "$nginx_directory" ] && [ ! -L "$nginx_directory" ] || exit 1
        [ "$(stat -c '%u' "$nginx_directory")" = 0 ] || exit 1
        nginx_directory_mode=$(stat -c '%a' "$nginx_directory")
        [ "$((0$nginx_directory_mode & 0022))" -eq 0 ] || exit 1
        chgrp nginx "$nginx_directory"
    fi
done
systemctl daemon-reload
systemd-analyze verify nginx.service
/usr/sbin/nginx -t -q -c /etc/nginx/nginx.conf -g 'daemon off;'
systemctl restart nginx.service
systemctl enable nginx.service
```

On SELinux-enforcing hosts, run `selinux/apply-nginx-policy` before the first
start so the unit's runtime directories are created with the right labels and
the QUIC listener and worker rlimits are permitted. The same-named unit in
`/etc/systemd/system` overrides the distribution unit; do not mask `nginx.service`,
which would prevent the replacement from starting too.

nginx now runs with `Type=exec` and `daemon off`, preserving `UMask=0027` in
the root master and its workers. nginx's daemonization otherwise calls
`umask(0)`. The unit supplies this global directive for start and configuration
checks; do not duplicate a `daemon` directive in the nginx configuration.

The unit deliberately leaves `User=` unset, using the system manager's default
root UID, and sets `Group=nginx` and `SupplementaryGroups=nginx`. The explicit
supplementary list removes inherited group zero access. An explicit `User=root`
triggers systemd 257's seccomp setup to drop `CAP_SETUID`, preventing nginx from
creating unprivileged workers under `NoNewPrivileges`; the default root UID
avoids that transition without adding ambient capabilities. The runtime
checker requires the root master to retain its exact reviewed capabilities and
only the nginx supplementary group. See the
[systemd execution source](https://github.com/systemd/systemd/blob/v257/src/core/exec-invoke.c#L4804-L4865).

`ExecStartPost=!` runs only the installed read-only checker with the manager's
root:root credentials, retaining its other sandbox and capability restrictions.
Group zero lets that checker inspect workers through `ProtectProc=invisible`;
the nginx master remains root:nginx without extra ptrace capability or root
supplementary-group access. The verifier waits up to
ten seconds for the managed PID file and at least one active worker, checking
master/worker identity, effective mask, capability sets, `NoNewPrivs`, seccomp
activation, and nginx's ability to traverse the root-owned log directory.
Failure fails service startup, so ordered dependents do not mistake successful
`execve` for readiness. The helper needs `setpriv` from `util-linux` and ordinary
Linux `/proc` access. Setup additionally checks a fresh master and exactly the
reviewed worker count: an explicit `--workers N` rejects both missing and extra
workers, while the default startup check requires at least one. Setup also
migrates existing root-owned directory groups without
recursing into their contents. Unexpected ownership, writable parents or
symbolic links fail setup before host mutation.

These are targeted observations, not proof of every sandbox or SELinux rule.
In particular, `Seccomp: 2` confirms a filter exists, not which syscalls it
denies. On the target Linux host, inspect the composed unit, effective mount
filter and capabilities, test intended reads/writes and upstream connections,
and exercise graceful reload and log rotation under representative traffic.

The ordinary capability allowlist covers privileged binds, worker identity
changes, log ownership and access, worker signals, and rlimits. Ambient
capabilities are empty; workers must have no effective or permitted capabilities.
`SystemCallFilter=~@mount` prevents privileged processes undoing the read-only
mount restrictions. A `quic_bpf` deployment explicitly installs
`nginx/templates/quic-bpf.service.conf` at
`/etc/systemd/system/nginx.service.d/10-quic-bpf.conf`, adding `CAP_NET_ADMIN`,
`CAP_BPF`, `CAP_PERFMON`, and the kernel-dependent `CAP_SYS_ADMIN` fallback.
The mount denial stays active in this profile. `nginx/setup --quic-bpf` selects
that extension and the existing SELinux BPF policy together; an ordinary apply
retires only an unchanged repository-owned capability drop-in. Locally changed
drop-ins require review before setup can proceed. Validate QUIC BPF on each
target kernel/nginx build with enforcing SELinux; do not add capabilities to
the ordinary profile to accommodate one optional feature.

Choose exactly one Certbot backend. Both use the repository's `certbot.timer`
and the common `certbot-healthcheck.timer`; do not leave a distribution or
Snap Certbot renewal timer active alongside the repository renewal timer.
When switching backends, stop `certbot.timer` before changing files. Reinstalling
the base timer does not remove anything under `certbot.timer.d/`.

For supported RHEL, Rocky Linux, and CentOS Stream hosts, prefer the
[`certbot/install`](../certbot/README.md) component installer. It handles EPEL,
the selected native or Snap payload, these unit files, the ACME webroot, and
the single-scheduler transition. The commands below remain the manual unit
installation reference.

### Native Certbot backend

Use this backend only when `/bin/certbot` is a native executable rather than a
Snap launcher:

```sh
install -m 0644 systemd/system/certbot.service /etc/systemd/system/certbot.service
install -m 0644 systemd/system/certbot.timer /etc/systemd/system/certbot.timer
install -m 0755 deploy/certbot-healthcheck /usr/local/bin/certbot-healthcheck
install -m 0644 \
    systemd/system/certbot-healthcheck.service \
    /etc/systemd/system/certbot-healthcheck.service
install -m 0644 \
    systemd/system/certbot-healthcheck.timer \
    /etc/systemd/system/certbot-healthcheck.timer
systemctl daemon-reload
systemctl enable --now certbot.timer certbot-healthcheck.timer
```

Without a `Unit=` directive, systemd derives `certbot.service` from the timer's
name and uses it as the oneshot payload. Do not install the Snap drop-ins with
this backend. When retiring the Snap backend, remove or park both of these
exact files before reloading systemd:

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
install -m 0755 deploy/certbot-healthcheck /usr/local/bin/certbot-healthcheck
install -m 0644 \
    systemd/system/certbot-healthcheck.service \
    /etc/systemd/system/certbot-healthcheck.service
install -m 0644 \
    systemd/system/certbot-healthcheck.timer \
    /etc/systemd/system/certbot-healthcheck.timer
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
systemctl enable --now certbot.timer certbot-healthcheck.timer
```

`snap stop --disable certbot.renew` disables Snap's automatic timer activation
for the renewal service; it does not uninstall or disable the Certbot CLI. The
repository timer can still start `snap.certbot.renew.service` explicitly. The
matching service drop-in restores the native backend's nginx ordering,
one-hour timeout, restrictive umask, partial-renewal reload, and visible
reload-command failure behavior. Successful preflight and HUP signaling do
not prove asynchronous configuration or certificate adoption by nginx;
inspect its error log and the certificate on a fresh TLS connection. Do not
also install an executable Certbot deploy hook that reloads nginx.

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
- For nginx, preserve the capability allowlist and mount-syscall denial.
  Optional QUIC BPF uses the managed extension described above. A different
  capability contract also requires reviewing the runtime verifier's expected
  sets; an unexpected local enlargement intentionally fails startup. A broader
  syscall allowlist remains a separate compatibility-tested change.
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
sudo systemd-analyze verify certbot-healthcheck.service certbot-healthcheck.timer
sudo systemd-analyze security nginx.service certbot.service
sudo systemctl start certbot.service
sudo systemctl --no-pager show certbot.service \
    -p Result -p ExecMainCode -p ExecMainStatus -p ExecStopPost
# Snap Certbot backend:
sudo systemd-analyze verify certbot.timer snap.certbot.renew.service
sudo systemd-analyze verify certbot-healthcheck.service certbot-healthcheck.timer
sudo systemctl start snap.certbot.renew.service
sudo systemctl --no-pager show snap.certbot.renew.service \
    -p Result -p ExecMainCode -p ExecMainStatus -p ExecStopPost -p DropInPaths
# Expected for Snap: repository timer enabled/active; Snap timer
# disabled/inactive.
sudo systemctl --no-pager show \
    certbot.timer snap.certbot.renew.timer \
    -p Id -p UnitFileState -p ActiveState
# Both backends:
sudo systemctl --no-pager show certbot.timer certbot-healthcheck.timer \
    -p Unit -p TimersCalendar -p RandomizedDelayUSec -p Persistent
sudo systemctl list-timers --no-pager | grep -Ei 'certbot|letsencrypt'
sudo systemd-analyze verify php-fpm@SITE_TAG.service
sudo systemd-analyze security nginx.service php-fpm@SITE_TAG.service
sudo certbot renew --dry-run \
    --server https://acme-staging-v02.api.letsencrypt.org/directory
sudo /usr/local/bin/certbot-healthcheck
sudo systemctl start certbot-healthcheck.service
```

The health timer checks every managed lineage daily and fails at a 30-day
threshold. Connect failed-unit state to the host's monitoring transport; the
unit intentionally contains no provider-specific alert credentials.

CI runs `systemd-analyze verify` for every push on Rocky Linux 9 and CentOS
Stream 10 and composes the optional Snap drop-ins against a representative
generated service. On a Snap host, repeat the single-scheduler and manual
service-start checks after Certbot Snap refreshes. `systemd-analyze security`
scores are advisory: this baseline deliberately keeps nginx's capabilities
unrestricted until validated per host, so read the report against the
documented rationale rather than chasing the number.
