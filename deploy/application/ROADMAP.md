# Application deployment standard roadmap

The transaction core is intentionally marked `core-only`. The following work
remains before the renderer may emit a conforming, installable version 1 bundle.

## Release gates

- Build a RHEL-family integration harness for OpenSSH, systemd path activation,
  SELinux policy compilation/effective permissions, restricted rsync, candidate
  preflight, live cutover, rollback, and recovery.
- Define and verify the private nginx site-adapter boundary without accepting
  domains, certificates, trusted proxy ranges, keys, or application-specific
  configuration in this repository.
- After those gates pass, exercise at least two independent private adopters
  without adding their profiles, adapters, hostnames, keys, or operational
  output to this repository.
- Tag version 1 only after the complete rendered bundle passes hostile-input,
  crash-recovery, rollback, and real non-production deployment audits.
