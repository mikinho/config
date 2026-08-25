# Certbot

This component installs Certbot on the repository's supported RHEL-family
bases and connects it to the hardened renewal policy under `systemd/`. The
installer supports RHEL 9 and 10, Rocky Linux 9 and 10, and CentOS Stream 9 and
10. Public CI exercises the currently deployed baselines, Rocky Linux 9 and
CentOS Stream 10.

The installer defaults to the native EPEL package because it can run inside
the repository's tightly confined `certbot.service`. The official Certbot Snap
is also supported and uses the snapd-generated renewal service plus the
repository's timer and nginx integration drop-ins. Choose one backend per
host; the installer fails if the other backend is still installed.

## Prerequisites

- Run this after the repository nginx baseline is installed. The installer
  requires `/usr/sbin/nginx`, the `nginx` group, and systemd.
- RHEL must be registered with access to its matching CodeReady Builder
  repository. Rocky Linux and CentOS Stream use their `crb` repository.
- The host needs outbound access to its configured DNF repositories and, for
  the Snap backend, the Snap Store.
- Review the plan on the target host before running it as root.

The installer enables the matching EPEL release through Fedora's stable
major-version permalink. It supports `x86_64` and `aarch64` hosts and rejects
unknown operating systems, unsupported major versions, and ambiguous CentOS
variants before installing anything.

## Install

Native EPEL backend:

```sh
certbot/install --plan
sudo certbot/install
```

Official Snap backend:

```sh
certbot/install --plan --backend snap
sudo certbot/install --backend snap
```

The installation performs these common actions:

- creates `/var/www/letsencrypt` as `root:nginx` with mode `0750`;
- installs the repository `certbot.timer` and the selected renewal payload;
- disables the package-owned renewal scheduler so exactly one timer remains;
- installs `certbot-healthcheck` under `/usr/local/bin`;
- verifies the composed systemd units; and
- enables the repository's twice-daily renewal timer.

The installer is safe to rerun for the selected backend. It refuses symbolic
link targets and will not delete a locally modified file while changing the
backend integration. It does not request a certificate or edit a site; those
actions require deployment-specific domain names and review.

## Request the first certificate

The nginx baseline already serves `/.well-known/acme-challenge/` from the
shared webroot. Request certificates without allowing Certbot to rewrite the
repository-managed nginx configuration:

```sh
sudo certbot certonly \
    --webroot \
    --webroot-path /var/www/letsencrypt \
    --domain example.com \
    --domain www.example.com
```

Add the resulting `/etc/letsencrypt/live/DOMAIN/` paths to the reviewed site
configuration, run `/usr/sbin/nginx -t`, and reload nginx only after that test
passes.

Then validate renewal and expiry monitoring:

```sh
sudo certbot renew --dry-run
sudo systemctl start certbot.service
sudo systemctl list-timers --no-pager | grep -Ei 'certbot|letsencrypt'
sudo /usr/local/bin/certbot-healthcheck
```

For the Snap backend, start `snap.certbot.renew.service` instead of
`certbot.service`. See [`../systemd/README.md`](../systemd/README.md) for the
complete backend contract, sandbox rationale, and runtime checks.

## Switching backends

Stop `certbot.timer` before switching. Remove the old Certbot payload with its
own package manager, then run this installer for the new backend:

```sh
# Native to Snap. Review the DNF transaction before confirming it.
sudo dnf remove certbot
sudo certbot/install --backend snap

# Snap to native. Remove only the Certbot Snap, not snapd used by other apps.
sudo snap remove certbot
sudo rm -f /usr/local/bin/certbot
sudo certbot/install --backend native
```

Certbot account, certificate, and renewal state under `/etc/letsencrypt` is
host state. Back it up before switching, and never commit it to this
repository.

## Upstream references

- [Certbot installation instructions](https://certbot.eff.org/instructions?ws=nginx&os=centosrhel8)
- [Fedora EPEL getting-started guide](https://docs.fedoraproject.org/en-US/epel/getting-started/)
- [Snap on Red Hat Enterprise Linux](https://snapcraft.io/docs/tutorials/install-the-daemon/red-hat/)
- [Snap on CentOS Stream](https://snapcraft.io/docs/tutorials/install-the-daemon/centos/)
- [Snap on Rocky Linux](https://snapcraft.io/docs/tutorials/install-the-daemon/rocky-linux/)
