# Application deployment standard roadmap

The transaction core is intentionally marked `core-only`. The following work
remains before the renderer may emit a conforming, installable version 1 bundle.

## Immutable release finalizer

- Define typed adapter contracts for build validation, dependency preparation,
  deployment metadata, and token-specific health checks.
- Generalize candidate validation, dependency provenance, isolated candidate
  service preflight, atomic release promotion, durable activation intent, live
  cutover, rollback, and interrupted-activation recovery.
- Make retained claims, terminal-result recovery, explicit requeue/discard, and
  release retention operate through one generic operator interface.
- Add crash-point tests across every durable state transition and prove that a
  client cannot receive success before committed live health is confirmed.

## Host-policy renderer

- Render the maintenance deny, forced-command SSH policy, live/candidate/
  finalizer/path/recovery systemd units, and root-owned secure tree.
- Render project-specific SELinux types and static-content labels from validated
  profile data without accepting arbitrary policy text.
- Generalize fail-closed setup and non-mutating verification, including exact
  owners, modes, link counts, effective SSH contexts, unit state, labels,
  dependency provenance, release pointers, and token-specific socket health.
- Integrate the shared nginx profile manifest without embedding domains,
  certificates, trusted proxy ranges, or other private host data.

## Conformance and release

- Build a RHEL-family integration harness for OpenSSH, systemd path activation,
  SELinux policy compilation/effective permissions, restricted rsync, candidate
  preflight, live cutover, rollback, and recovery.
- Add a conformance command that compares a private project's vendored bundle
  to its private profile and the pinned standard revision.
- Exercise at least two independent private adopters without adding their
  profiles, adapters, hostnames, keys, or operational output to this repository.
- Tag version 1 only after the complete rendered bundle passes hostile-input,
  crash-recovery, rollback, and real non-production deployment audits.

