# logrotate policy

Daily rotation for nginx's file logs with fourteen kept generations,
compression deferred one cycle, and a signaled reopen.

## Installation

```sh
install -m 0644 logrotate/nginx /etc/logrotate.d/nginx
```

The nginx.org package installs its own `/etc/logrotate.d/nginx`. Replace it
with this policy rather than letting two definitions cover the same files —
duplicate coverage is a logrotate error.

## Behavior

- `postrotate` sends `USR1` through
  `systemctl kill --kill-who=main --signal=USR1 nginx.service`, which tells
  the nginx master to reopen log files. Routing the signal through systemd
  avoids trusting a PID file and does nothing when the service is inactive.
- `create 0640 nginx nginx` matches how the nginx master chowns reopened
  logs for its workers, and stays consistent with the unit's `LogsDirectory`
  handling and `UMask`.
- `delaycompress` keeps the most recent rotated file uncompressed, which the
  master may still write into between rotation and the reopen signal.
- Rotation is compatible with the fail2ban jails: they tail the live file
  names, which `create` preserves across rotation.

## Validation

```sh
sudo logrotate --debug /etc/logrotate.d/nginx
```

CI runs the same debug pass on Rocky Linux 9 and CentOS Stream 10 for every
push.
