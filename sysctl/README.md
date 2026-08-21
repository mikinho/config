# sysctl policy

Kernel tunables the baseline depends on. Without them nothing fails loudly —
the kernel silently caps the QUIC listeners' requested socket buffers at the
default maximums, and HTTP/3 throughput degrades under load.

## Installation

```sh
install -m 0644 sysctl/99-nginx-quic.conf /etc/sysctl.d/99-nginx-quic.conf
sysctl --system
```

`sysctl --system` applies every configured directory in precedence order and
is safe to re-run; settings also persist across reboots once the file is in
place. Restart nginx afterward so the QUIC listeners are re-created under the
raised limits.

## Validation

```sh
sysctl net.core.rmem_max net.core.wmem_max
```

Both must report at least `2097152`, matching the `rcvbuf=2m sndbuf=2m`
listener parameters in `sites/_https_.conf`. Change the listener parameters
and these limits together.

A host profile that already sets higher maximums — a tuned profile or another
service's drop-in — wins if it sorts later in `/etc/sysctl.d/`; that is fine,
since only the minimum matters here. Do not lower a higher deployment value
to match this file.
