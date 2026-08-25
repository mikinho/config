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
- Subscription-managed RHEL must be registered with access to its matching
  CodeReady Builder repository. A RHEL RHUI image must expose its current
  non-EUS CodeReady Builder RHUI repository. Rocky Linux and CentOS Stream use
  their `crb` repository.
- The host needs outbound access to its configured DNF repositories and, for
  the Snap backend, the Snap Store.
- Review the plan on the target host before running it as root.

The installer enables the matching EPEL release through Fedora's stable
major-version permalink. It supports `x86_64` and `aarch64` hosts and rejects
unknown operating systems, unsupported major versions, and ambiguous CentOS
variants before installing anything. On RHEL it reuses an enabled CodeReady
Builder repository, enables a discovered RHUI variant with DNF, or uses
`subscription-manager` for a subscription-managed host. It fails closed when
the configured RHUI does not publish CodeReady Builder. Native EPEL packages
are not installed while EUS or E4S repositories are enabled: those streams
pin a RHEL minor release while EPEL advances within the major release, so its
dependencies can move beyond the pinned RHEL content. Switch an Azure VM to
non-EUS RHUI or provide a reviewed package source built for the pinned minor
release instead of mixing repository streams.

## Install

`setup` is the standard component entry point used by the host orchestrator.
For Certbot it delegates to `install`, because the payload, webroot, timer,
service/drop-ins, and healthcheck form one atomic runtime contract. The two
commands therefore accept the same options; use `setup` in standardized host
workflows and `install` when working on this component directly.

Native EPEL backend:

```sh
certbot/install --plan
sudo certbot/install

# Equivalent standardized entry point:
certbot/setup --plan
sudo certbot/setup
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
- installs and enables a daily certificate-health timer with a 30-day warning
  threshold;
- verifies the composed systemd units; and
- enables the repository's twice-daily renewal timer.

The installer is safe to rerun for the selected backend. It refuses symbolic
link targets and will not delete a locally modified file while changing the
backend integration. It does not request a certificate or edit a site; those
actions require deployment-specific domain names and review.

## Issue the first certificate

The nginx baseline already serves `/.well-known/acme-challenge/` from the
shared webroot. Use `issue` so the first request is plan-first, proves the
local HTTP-01 path, and cannot silently target production before the same
names pass Let's Encrypt staging. Repeat `--domain` in the intended lineage
order; the first name becomes the Certbot lineage name.

```sh
certbot/issue --plan --staging \
    --email admin@example.com \
    --domain example.com \
    --domain www.example.com
sudo certbot/issue --staging \
    --email admin@example.com \
    --domain example.com \
    --domain www.example.com

certbot/issue --plan --production --staging-passed \
    --email admin@example.com \
    --domain example.com \
    --domain www.example.com
sudo certbot/issue --production --staging-passed \
    --email admin@example.com \
    --domain example.com \
    --domain www.example.com
```

Add `--backend snap` to every command when that backend is installed. Staging
uses Certbot's non-persistent `--dry-run`; `--staging-passed` is an explicit
operator assertion that the production names match the successful test. The
helper rejects wildcard names because they require a deployment-specific
DNS-01 plugin and protected provider credentials.

Add the resulting `/etc/letsencrypt/live/DOMAIN/` paths to the reviewed site
configuration, run `/usr/sbin/nginx -t`, and reload nginx only after that test
passes.

The helper refuses an existing lineage. Change lineage-specific settings with
`certbot reconfigure`; do not hand-edit files under
`/etc/letsencrypt/renewal/`. Do not place domains or per-site authenticator
settings in a global `cli.ini`, because those defaults affect every Certbot
invocation on the host.

Then validate renewal and expiry monitoring:

```sh
sudo certbot renew --dry-run
sudo systemctl start certbot.service
sudo systemctl list-timers --no-pager | grep -Ei 'certbot|letsencrypt'
sudo /usr/local/bin/certbot-healthcheck
sudo systemctl start certbot-healthcheck.service
```

For the Snap backend, start `snap.certbot.renew.service` instead of
`certbot.service`. See [`../systemd/README.md`](../systemd/README.md) for the
complete backend contract, sandbox rationale, and runtime checks.

The daily health service exits nonzero when any managed certificate is
missing, invalid, or within 30 days of expiry. It intentionally has no email,
webhook, or vendor-specific notification credential; host monitoring must
alert on a failed `certbot-healthcheck.service` unit.

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
