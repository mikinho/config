# logrotate policy

Daily rotation for nginx's global, per-site, and conditional static-asset
failure logs with fourteen kept generations, compression deferred one cycle,
and a signaled reopen.

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
- `create 0640 nginx nginx` gives workers access when they independently reopen
  logs after USR1. The unit manages their parent as `root:nginx 0750`, providing
  traversal while keeping the directory unwritable by workers. A `root:root
  0750` parent would block reopening despite correct file ownership.
  Foreground nginx preserves `UMask=0027`, so missing logs recreated by nginx
  also start at `0640`; daemon mode would reset that mask to zero.
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

The nginx runtime fixture also reproduces the rename/create/USR1 sequence,
checks that every active worker switches to the new log, then checks that new
requests grow only the current file. It separately tests missing-file
recreation and mask retention after reload. This container test does not
exercise the systemd signal dispatch or full service sandbox. On a booted
validation host, test the actual installed logrotate policy with representative
traffic and confirm Fail2ban continues consuming the live log names.
