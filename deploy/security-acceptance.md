# Security acceptance on Linux test hosts

Repository checks validate portable logic and configuration contracts. A
completed deployment also needs the selected services exercised under the
actual systemd, kernel, package build, and enforcing SELinux policy. Keep host
identities, network allowlists, certificates, and resulting evidence in the
private operational record; this public checklist contains only reusable steps.

Markdown is the canonical documentation. Generate or refresh a PDF only when
requested; a previously generated PDF is a snapshot, not current acceptance
evidence.

## Read-only inspection of an existing host

Identify the installed component and baseline revision first. Inspect the
effective unit rather than assuming a repository file is active:

```sh
systemctl --version
getenforce
sudo systemctl show nginx.service \
  -p FragmentPath -p DropInPaths -p Type -p User -p Group -p MainPID \
  -p UMask -p NoNewPrivileges -p ProtectSystem -p CapabilityBoundingSet \
  -p SystemCallFilter -p RuntimeDirectoryMode -p LogsDirectoryMode
sudo stat -Lc '%U:%G %a %n' /run/nginx /var/lib/nginx /var/log/nginx
sudo getfacl -cp /var/log/nginx
```

Run the installed component verifier and inspect each running main process's
`Umask`, `NoNewPrivs`, capability fields, and SELinux domain. For nginx, inspect
workers as well as the master. A loaded `UMask` value does not prove a daemon
retained that mask or that an old process adopted a changed unit. An inactive
oneshot service has no process mask to inspect; validate its behavior during
the disposable-host lifecycle tests below.

Read-only inspection does not authorize changing the existing host's service,
rotating its logs, issuing certificates, or restarting a workload. Perform
those lifecycle checks on disposable hosts or within an authorized maintenance
window. Do not publish full nginx configuration, environment values, private
keys, or request logs in public CI output.

## Disposable-host test matrix

| Surface | Host prerequisites | Required behavior |
| --- | --- | --- |
| nginx | Supported EL9/EL10 host, nginx build, systemd, SELinux enforcing | Start, reload, restart, failed startup, log rotation under traffic, repeated rotation, HTTP/2 and HTTP/3, optional BPF profile. |
| PHP-FPM | Site identity, supported FPM build, nginx, socket ACL support | Authorized nginx connection; unrelated-user denial; public upload readability; private session and temporary-file protection. |
| Monit | Supported EPEL package, existing baseline and package-only fixtures | Fresh adoption; changed execution-policy restart; configuration-only reload; strict fragment acceptance; alert delivery; durable backups; complete and incomplete rollback. |
| Smallstep | Dedicated test CA host, disposable offline-root/intermediate and provisioner keys | Private-JWK rejection; endpoint disclosure test; lifetime boundary issuance; policy-denied names; renewal; expired renewal denial; loaded policy and client-network enforcement. |
| Redis | Supported pinned package, local/network profiles, disposable data | Private administrative socket, effective allowlist and mask, denied unauthorized clients, short-lived certificate reload, unchanged main PID, failed reload preserving service. |
| MongoDB | Supported package, disposable initialized replica set and users | Authentication/authorization denials, backup roles, TLS identity and renewal threshold, file creation with the selected honorSystemUmask/processUmask policy. |
| Application deployment | Dedicated host meeting the gold standard's prerequisites | Private finalizer/recovery defaults, group-readable terminal results, candidate readiness, promotion, interrupted activation, rollback, and recovery. |

Use both Rocky Linux 9 and the supported newer EL target where a component
claims compatibility. Record exact package versions and selected profiles.
Running nginx or PHP directly in a container does not validate the systemd
directory, capability, cgroup, or SELinux contract.

## Permissions and process-policy acceptance

- Verify expected owner, group, mode, link count, and ACLs on protected files
  and their parent directories. Defaults do not repair old files or remove
  unexpected ACLs. Confirm access using the intended reader and an unrelated
  identity, in addition to inspecting mode bits.
- Preserve `0077` for private services. The nginx policy uses `0027` with
  foreground supervision. PHP-FPM's `0022` exception is for public assets;
  private outputs remain beneath protected directories or use explicit modes.
- Check configured and running masks after start, reload, and restart.
  Programs may change their own mask. Monit child commands establish their
  own defaults; scripts handling private files must set an explicit mask.
- On a disposable host, add a temporary weakening drop-in and require the
  verifier to reject it. Test both removed restrictions and broadened allowed
  networks: a default-deny property alone does not prove the allowed set is
  correct. Remove the test override and revalidate before accepting the host.
- For nginx, edit the unit or a drop-in without `systemctl daemon-reload` and
  restart the service. The start must succeed with the gate's stale-metadata
  warning in the journal, and `verify-deployment` must fail until
  `daemon-reload` and a further restart activate the reviewed change. A start
  gate that fails here converts an operator omission into an outage.
- Rotate nginx logs while requests are arriving. Prove new files receive
  records and old inodes stop growing. Verify log-consumer continuity and
  repeat after a service restart. Signal success alone is insufficient.
- Exercise required privileged operations under the capability and syscall
  policy. Optional QUIC BPF permissions must remain explicit and tested;
  do not widen every nginx deployment to accommodate one optional profile.

## Certificate and recovery acceptance

Use disposable certificate material for negative tests. Confirm CA limits at
the effective provisioner level and reject unreviewed templates or options.
Service-specific minimum remaining lifetime must allow the selected issuance
and automated-renewal schedule to compose. Record the renewal interval,
failure-alert threshold, and recovery margin rather than choosing a long
lifetime solely to bypass a verifier.

For each state-changing setup, retain root-only transaction evidence until
service and notification acceptance is complete. Test failures while writing,
activating, and restoring. An incomplete rollback must report failure and
preserve its recovery files; a successful syntax check does not establish
successful restoration.

## Operational controls outside component verification

Nonpersistent interactive shell history minimizes residual data. It does not
replace execution evidence. Record and verify the selected Linux Audit,
privilege-escalation, session-recording, centralized logging, and retention
controls separately. Avoid secrets in command arguments regardless of the
recording policy. Reviewed source documentation records intended operations,
not proof that those operations occurred.

Inspect core-dump handling for secret-bearing services. A unit's `LimitCORE`
alone does not establish the host collector's storage policy. Review the
effective systemd-coredump configuration, storage access, retention, and
approved diagnostic exception procedure without opening existing dumps.

Package pinning supports reproducibility but does not establish that packages
remain current or free of known vulnerabilities. Review vendor security
advisories for installed builds and track approved updates. Use
`systemd-analyze security` to identify candidate hardening work, not as a
standalone security or compliance verdict.

Relevant primary references: [systemd execution settings](https://github.com/systemd/systemd/blob/v252/man/systemd.exec.xml),
[nginx log rotation](https://nginx.org/en/docs/control.html#logs),
[Smallstep configuration](https://smallstep.com/docs/step-ca/configuration/),
[MongoDB parameters](https://www.mongodb.com/docs/manual/reference/parameters/),
and [Red Hat Linux Audit](https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/9/html/security_hardening/auditing-the-system_security-hardening).
