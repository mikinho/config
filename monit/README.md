# Monit operations standard

This component installs and configures a client-neutral Monit baseline for
RHEL-family virtual machines. It keeps SELinux enforcing, uses the signed EPEL
package, rotates a private file log without restarting Monit, exposes the
control API only on IPv4 loopback, and leaves application restart ownership
with systemd unless a deployment explicitly approves a different action.

Hostnames, notification recipients, mail or M/Monit credentials, application
names, addresses, and deployment evidence do not belong in this public
repository. Supply them as protected deployment fragments and retain their
source and acceptance evidence in the relevant private system of record.

## Supported platform and version policy

The installer supports the repository's RHEL-family baseline: RHEL, Rocky
Linux, and CentOS Stream major versions 9 and 10 on x86-64 or ARM64. It enables
the appropriate EPEL repository through the shared platform helper and
installs the repository's current signed `monit` package.

The supported compatibility floor is Monit 5.35.2 and the preferred release is
6.0.0. EPEL 9 supplies 6.0.0, while EPEL 10.2 supplies 5.35.2 as of August 31,
2026. The installer deliberately follows the enabled, signed EPEL stream and
reports which policy tier was installed. It does not add a subscription
repository, copy a licensed package from another host, use a package built for
a different Enterprise Linux minor stream, or compile a replacement locally.

Monit 6.0.0 fixes event-state handling when multiple resource tests share an
event type, which is relevant to the generic resource policy in this baseline.
Use 6.0.0 wherever the supported distribution stream provides it. The 5.35.2
compatibility tier keeps EL10.2 on its supported package; deployments using
that tier should retain independent host telemetry to cover the resource-alert
limitation. Advance an EL10.2 host only after its own EPEL stream publishes a
signed 6.0.0-or-newer package or after a separately reviewed package-promotion
path is available.

The verifier rejects a downgrade below 5.35.2, identifies compatibility-tier
installs in its output, and checks the installed binary and vendor systemd unit
with RPM verification. Running `monit/install` again follows the enabled EPEL
stream, so the normal package transaction adopts 6.0.0 when it becomes
available without a repository-policy change.

## Security and ownership model

- `/etc/monitrc` is `root:root` mode `0600`. Setup renders a random 256-bit
  hexadecimal password for the local control client and preserves that value
  on later managed reruns.
- TCP 2812 binds only to `127.0.0.1`. There is no public Monit dashboard or
  management port. Root uses the protected control file for `monit summary`,
  `monit status`, and other client commands.
- `/etc/monit.d/*.conf` fragments are `root:root` mode `0600`. Existing matching
  fragments are preserved only after checking ownership, permissions, a single
  hardlink, and the absence of extended ACLs. Fragment directories must have no
  extended or default ACL. A requested fragment explicitly adopts its basename
  from a validated staged copy, replacing the old inode and severing aliases.
  Symbolic links and nested include directives are rejected before activation.
- The public resource fragment alerts on sustained load, CPU, memory, swap,
  root-filesystem space, and inode pressure. It contains no start, stop, or
  restart action.
- systemd owns Monit's own recovery through `Restart=on-failure`. The drop-in
  creates private runtime, state, and log directories and applies `UMask=0077`.
  Acceptance checks both the configured mask and the running daemon's
  `/proc/MAINPID/status` `Umask` field. A changed drop-in or stale process mask
  requires a controlled Monit restart; configuration-only updates use reload.
- Notification transport and recipients are deployment secrets. Place them in
  a separate protected fragment; never add them to the public template, a
  command line, a rendered plan, or an evidence record.

The service mask protects files created by the Monit daemon. Monit 6.0.0's
command runner explicitly sets `0022` for spawned check/action programs, so
those programs do not inherit the daemon's `0077` policy. Any reviewed helper
that creates private files must set its own `umask 077` before writing and use
explicit modes where needed. Keep service management behind systemd and review
the effective execution policy before adding an action.

Monit is intentionally a privileged observer. A general monitor may need to
read process state, sockets, files, filesystems, and service-specific paths,
so this baseline does not invent a broad local SELinux policy module or run
`audit2allow`. It uses the distribution's default file contexts, restores those
labels during setup, requires Enforcing mode, rejects `unconfined_t`, and fails
acceptance if the audit log contains a Monit AVC denial since boot. Investigate
and narrowly resolve a real denial rather than disabling SELinux or installing
generated allow rules without review.

## Installed layout

| Path | Purpose and identity |
| --- | --- |
| `/etc/monitrc` | Managed global control file; `root:root` mode `0600` |
| `/etc/monit.d/10-system.conf` | Client-neutral alert-only VM checks; `root:root` mode `0600` |
| `/etc/monit.d/*.conf` | Preserved or explicitly supplied deployment fragments; `root:root` mode `0600` |
| `/etc/logrotate.d/monit` | Daily/25 MiB rotation, 14 archives, compression, and SIGHUP reopen |
| `/etc/systemd/system/monit.service.d/10-baseline.conf` | Runtime directories, umask, restart, and signal reload policy |
| `/var/log/monit/monit.log` | Private Monit file log; `root:root` mode `0600` |
| `/var/lib/monit` | Persistent ID, state, and queued events; `root:root` mode `0700` |
| `/run/monit` | Runtime PID directory; `root:root` mode `0750` |
| `/usr/local/bin/verify-monit` | Installed read-only acceptance verifier |
| `/usr/local/libexec/config-monit/version.sh` | Installed compatibility and preferred-version policy used by the verifier |
| `/usr/local/libexec/config-monit/security.sh` | Shared fragment, ACL, and process-umask validation |
| `/var/lib/config-monit/backups/transaction.*` | Retained root-only transaction originals, target manifest, status, and previous service state |

The main control file includes only `/etc/monit.d/*.conf`. Staging files must
therefore use a different suffix or a directory outside `/etc/monit.d`; a
partially written `.conf` must never become visible to the live include glob.

## Install

Review package and repository actions without changing the host:

```sh
monit/install --plan
```

Install Monit and its audit, SELinux, logrotate, OpenSSL, process, and network
inspection dependencies:

```sh
sudo monit/install
```

The installer includes `getfacl` from the `acl` package. It preserves an existing
service's active/enabled state. A fresh
installation remains stopped and disabled until setup validates a complete
candidate. It also installs `verify-monit` and the exact public policy sources
the installed verifier compares against.

## Render and review setup

Render a self-contained install tree without touching the host:

```sh
monit/setup --output /tmp/monit-baseline
```

Add one or more deployment fragments to the candidate by their safe basename:

```sh
monit/setup --output /tmp/monit-host \
  --fragment /private/reviewed/50-notifications.conf \
  --fragment /private/reviewed/70-service.conf
```

The output contains a generated local control credential and is therefore a
protected deployment artifact. Review it, keep it out of source control, and
remove ordinary staging copies after promotion.

Preview the live action separately:

```sh
monit/setup --plan \
  --fragment /private/reviewed/50-notifications.conf \
  --fragment /private/reviewed/70-service.conf
```

## Apply and adopt

For a fresh package configuration or a host already managed by this standard:

```sh
sudo monit/setup \
  --fragment /private/reviewed/50-notifications.conf \
  --fragment /private/reviewed/70-service.conf
```

Setup assembles the candidate with every existing live `*.conf` fragment,
parses the full tree, validates logrotate, takes recoverable copies of every
managed target, installs atomically by file, restores SELinux labels, reloads
systemd, and then activates Monit. Existing daemons reload for configuration-only
changes. A changed systemd drop-in or a running mask other than `0077` requires
a controlled restart of Monit so execution settings take effect. Fresh daemons
are enabled and started. The complete verifier checks the running process mask
before committing the transaction. Failure attempts to restore the managed
files and prior service enablement/activity state; incomplete recovery is an
explicit error with retained originals for operator repair.

The EPEL package's unmodified example `/etc/monitrc` may be replaced safely.
Any other unmanaged modification is a migration boundary because it may hold
notification secrets or service policy. Move those directives to reviewed
root-only `.conf` fragments, render the combined candidate, and only then make
the adoption explicit:

```sh
sudo monit/setup --replace-main \
  --fragment /private/reviewed/50-notifications.conf
```

`--replace-main` authorizes replacement of only `/etc/monitrc`; it does not
delete unrelated fragments. Keep the setup backup until notification delivery
and all service checks have passed acceptance.

To adopt a trusted root-owned fragment currently at `0644` or `0640`, run
`monit/install` first if `getfacl` is missing, then explicitly supply that same
live path with `monit/setup --fragment /etc/monit.d/50-notifications.conf`.
The source and its canonical parent directories must be `root:root`, have no
group/other write bits or extended ACLs, and the file must have one hardlink.
Use a protected root directory for off-tree adoption sources. Setup validates
a staged copy and promotes it as `0600`.
An original with an untrusted owner, writable access, aliases, or ACLs requires
a separately reviewed independent root-owned source before adoption; passing
the unsafe original directly is rejected. Directory ACLs require separate
explicit remediation before setup. Setup never
silently follows a symbolic link or an external include tree. The main file
must match the managed template apart from its generated credential and include
path; deployment policy belongs in the directly included fragments.

## Deployment fragment contract

A deployment fragment must be non-symbolic, end in `.conf`, and use a basename
containing only letters, numbers, dots, underscores, and hyphens. The reserved
name `10-system.conf` cannot be supplied. Each fragment must be independently
meaningful and contain no nested `include` directive. Keep global notification
policy in an earlier fragment and service checks in later clearly named
fragments, but do not rely on wildcard include order for cross-file definitions:
Monit documents glob inclusion as unsorted. Flatten an existing nested tree into
reviewed, directly supplied fragments before adoption. Reserve the unquoted
`include` token for the managed main file; quote literal uses in fragment values.

Prefer alert-only checks. A service already managed by systemd should not also
be restarted by Monit without an explicit failure-amplification analysis. In
particular, a shared database or network outage must not trigger restart loops
across otherwise healthy applications.

Before promotion, parse the exact assembled tree with the target host's Monit
binary. Avoid control forms introduced after the supported floor. A route or
socket check should use explicit timeouts, consecutive failed cycles, and a
recovery notification. Do not stop a production service merely to test alert
delivery; use a temporary controlled failing check and then observe recovery.

## Log rotation and SELinux

Monit writes `/var/log/monit/monit.log`. Logrotate runs daily or when the log
exceeds 25 MiB, retains 14 dated archives, compresses after one cycle, creates
the new log as `root:root` mode `0600`, and sends SIGHUP to the active service.
The upstream Monit signal contract reopens logs and rereads configuration, so
rotation does not restart the daemon or a monitored application.

Setup restores the policy-default labels on configuration, systemd, state,
runtime, and log paths. Run the verifier after first start and after adding a
check that reads a new service-specific path. If it reports a denial, collect
the exact AVC, confirm the intended read or action, and prefer an existing
distribution label or interface. Do not use permissive mode as a deployment
step.

## Verify and collect acceptance evidence

Run the installed verifier as root:

```sh
sudo /usr/local/bin/verify-monit
```

Acceptance additionally requires deployment-specific checks:

1. `monit summary` and every intended `monit status NAME` report monitored and
   healthy after at least three collection cycles.
2. The public or private service path being monitored has the intended failure
   meaning and does not expose credentials in output.
3. A controlled failure alert and its recovery notification are received
   through the protected transport.
4. Monit's main PID is unchanged by configuration-only promotion and log
   rotation. Execution-policy promotion changes Monit's PID and the new daemon
   reports `Umask: 0077`; monitored application PIDs remain unchanged.
5. `ausearch -m AVC,USER_AVC -ts boot -c monit` returns no denial.
6. The only TCP 2812 listener is `127.0.0.1:2812`; no host or cloud firewall
   rule exposes it.
7. The private evidence record captures package NEVRA, policy revision,
   sanitized status, alert receipt, PID continuity, and rollback owner.

## Rollback

Setup prints its retained transaction path under
`/var/lib/config-monit/backups/transaction.*`. The directory is `root:root` mode
`0700`; it contains protected originals under `files`, existence records under
`present` and `absent`, a `targets` manifest, `service-state`, and a `status`
record. GNU metadata-preserving copies retain modes, ownership, timestamps,
ACLs, and SELinux contexts. Restoration replaces files atomically and does not
reconnect old hardlink aliases.

Automatic rollback attempts every target and reports `rollback-incomplete` if
any file or service restoration fails. The originals remain available after
success, failure, or incomplete recovery; ordinary candidate cleanup does not
remove them. An incomplete file restoration leaves Monit stopped when possible
so it cannot consume a partially restored tree. Once every file is restored,
the restored tree is authoritative: a failed `systemctl daemon-reload` or
service command is still reported as `rollback-incomplete`, but rollback
re-establishes the recorded service state rather than stopping a running
daemon, and the operator completes recovery with `daemon-reload` followed by a
restart or reload. If the restored configuration itself fails `monit -t`, the
service state is left unchanged for the operator. Inspect the error and
transaction records, repair the cause, and restore the recorded originals
before activation.

For a later operator rollback, use `targets` and the `present`/`absent` records
to restore the previous files and remove targets that did not previously exist.
Preserve their recorded metadata. If only Monit configuration changed, run:

```sh
sudo systemctl daemon-reload
sudo monit -t -c /etc/monitrc
sudo systemctl reload monit.service
```

If the systemd execution policy changed, use `systemctl restart monit.service`
after `daemon-reload` and syntax validation instead of reload. Restore the
recorded active/enabled state from `service-state`, then repeat deployment
acceptance. Retain the root-only transaction until alert delivery, service
checks, and the rollback retention decision have been recorded privately;
remove it only after that decision. Backup contents can include secrets and
must never be copied into this repository or ordinary audit output.

If Monit was a fresh deployment and must be removed from service, disable and
stop `monit.service` first. Package removal is a separate reviewed action; do
not delete state, logs, fragments, or evidence until their retention decision
is recorded.

## Reproduce the PDF handoff

The Markdown file is canonical. Validate it without ReportLab:

```sh
python3 monit/build-operations-standard-pdf.py --check
```

Build the delivery artifact when ReportLab is available:

```sh
python3 monit/build-operations-standard-pdf.py
```

Render every page with Poppler and inspect the PNGs before delivery. Text
extraction alone does not prove that tables, code, or page transitions are
legible.

## Official references

- [Monit manual](https://mmonit.com/monit/documentation/monit.html)
- [Monit 6.0.0 source, including the command runner's explicit umask](https://mmonit.com/monit/dist/monit-6.0.0.tar.gz)
- [systemd execution environment and UMask](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html)
- [Fedora package metadata for Monit](https://packages.fedoraproject.org/pkgs/monit/monit/)
- [Red Hat SELinux administration](https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/9/html/using_selinux/index)
- [logrotate manual](https://man7.org/linux/man-pages/man8/logrotate.8.html)
