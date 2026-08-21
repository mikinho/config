# fail2ban baseline

Optional intrusion-ban policy for hosts that receive client traffic directly.
It bans at the local firewall based on the addresses nginx logs, so it is
only effective when those addresses are the true remote peers.

**Do not enable these nginx jails behind a CDN or external load balancer.**
With `stubs/http/realip.conf`, nginx logs the restored client address, but
packets still arrive from the proxy: a firewall ban on the client address
blocks nothing, and without address restoration the jail would ban the proxy
itself. Fronted deployments need enforcement at the edge (provider API or an
nginx-level deny), which is out of scope for this shared policy. The `sshd`
jail is unaffected and appropriate everywhere.

## Installation

fail2ban ships in EPEL on RHEL-family hosts:

```sh
dnf install epel-release
dnf install fail2ban fail2ban-firewalld fail2ban-selinux
```

`fail2ban-firewalld` selects the firewalld ban action through its own
`jail.d` drop-in, and `fail2ban-selinux` provides the enforcing-mode policy.
Install the shared files, then enable the service:

```sh
install -m 0644 fail2ban/jail.local /etc/fail2ban/jail.local
install -m 0644 fail2ban/filter.d/nginx-444.conf /etc/fail2ban/filter.d/nginx-444.conf
systemctl enable --now fail2ban.service
```

Deployment-specific settings — administrative `ignoreip` ranges, per-host
jail toggles — belong in a local `/etc/fail2ban/jail.d/*.local` drop-in, the
same pattern as `nginx/trusted-proxies/`. Never commit them here. Confirm an
administrative range is in `ignoreip` before enabling the `sshd` jail on a
remote host; a typo in an aggressive jail can lock the operator out.

## Jails

| Jail | Signal | Source |
| --- | --- | --- |
| `sshd` | Failed SSH authentication, via the systemd journal. | sshd |
| `nginx-http-auth` | Failed HTTP basic authentication. | `/var/log/nginx/error.log` |
| `nginx-limit-req` | Sustained `limit_req`/`limit_conn` violations. The baseline's `limit_req_log_level warn` writes these events where the stock filter finds them. | `/var/log/nginx/error.log` |
| `nginx-444` | Requests for host names this deployment does not serve, answered 444 by `sites/_http_.conf`. Custom filter in `filter.d/`. | `/var/log/nginx/access.log` |

The `nginx-444` filter deliberately ignores status 421 from the HTTPS default
server: HTTP/2 and HTTP/3 connection reuse can produce legitimate 421
responses that clients transparently retry, so banning on them risks real
users.

## Validation

```sh
fail2ban-client -t
fail2ban-regex /var/log/nginx/access.log /etc/fail2ban/filter.d/nginx-444.conf
fail2ban-client status
fail2ban-client status nginx-444
```

`fail2ban-client -t` checks the merged configuration without restarting.
`fail2ban-regex` reports how many lines the filter matched; run it against a
log that contains known scanner traffic before trusting the jail. Unban a
mistake with `fail2ban-client set JAIL unbanip ADDRESS`.

Log rotation is compatible: the jails tail the live files and
`logrotate/nginx` rotates with `create`, which fail2ban's file backend
follows across rotation.
