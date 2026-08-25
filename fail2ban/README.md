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

## Installation and setup

`install` detects RHEL, Rocky Linux, or CentOS Stream major version 9 or 10,
enables the matching EPEL/CRB repositories, and installs `fail2ban`,
`fail2ban-firewalld`, `fail2ban-selinux`, and the Python runtime used for exact
CIDR validation. It deliberately leaves the service inactive until a
deployment topology and administrative CIDR have been selected:

```sh
fail2ban/install --plan
sudo fail2ban/install
```

`fail2ban-firewalld` selects the firewalld ban action through its own
`jail.d` drop-in, and `fail2ban-selinux` provides the enforcing-mode policy.
Apply the repository policy with `setup`, repeating `--ignore-ip` for every
trusted administrative network:

```sh
# Direct client connections; SSH is still transitioning from 22 to 2356.
fail2ban/setup --plan \
    --topology direct \
    --ssh-phase prepare \
    --ignore-ip 192.0.2.0/24
sudo fail2ban/setup \
    --topology direct \
    --ssh-phase prepare \
    --ignore-ip 192.0.2.0/24

# After SSH finalization:
sudo fail2ban/setup \
    --topology direct \
    --ssh-phase final \
    --ignore-ip 192.0.2.0/24
```

Use `--topology proxied` on a CDN or load-balancer origin. That mode enables
only `sshd`; all nginx jails remain disabled. The shared `jail.local` also
keeps every jail disabled so copying a file cannot accidentally activate a
ban policy. `setup` renders the explicit host enablement in
`jail.d/90-baseline.local`, validates it with `fail2ban-client -t`, restores
the previous files if validation or activation fails, and only then enables
the service.

Administrative addresses must use explicit CIDR notation. They are host
state, supplied at setup time, and must not be committed to this repository.
The setup tool refuses to enable any jail without at least one administrative
CIDR. Render a candidate without changing a host by replacing `--plan` with
`--output DIRECTORY`.

## Jails

| Jail | Signal | Source |
| --- | --- | --- |
| `sshd` | Failed SSH authentication, via the systemd journal. | sshd |
| `nginx-http-auth` | Failed HTTP basic authentication. | `/var/log/nginx/*error.log` |
| `nginx-limit-req` | Sustained `limit_req`/`limit_conn` violations. The baseline's `limit_req_log_level warn` writes these events where the stock filter finds them. | `/var/log/nginx/*error.log` |
| `nginx-444` | Requests for host names this deployment does not serve, answered 444 by `sites/_http_.conf`. Custom filter in `filter.d/`. | `/var/log/nginx/access.log` |

The `nginx-444` filter deliberately ignores status 421 from the HTTPS default
server: HTTP/2 and HTTP/3 connection reuse can produce legitimate 421
responses that clients transparently retry, so banning on them risks real
users.

The error-log glob covers the baseline global log and per-site error logs such
as the public WordPress sample. Keep site logs under `/var/log/nginx` or add a
deployment-local jail override for any deliberate alternate location. fail2ban
expands the glob when the service starts or reloads, so reload it after a new
site log first appears.

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
