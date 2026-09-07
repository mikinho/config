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
uses Certbot's non-persistent `--dry-run` with the explicit Let's Encrypt
staging endpoint. Production issuance explicitly selects the production
endpoint; its follow-up renewal dry run selects staging again. These command
line selections override a conflicting `server` default in `cli.ini`.
The helper also rejects `staging`, `test-cert`, and `dry-run` directives in
`/etc/letsencrypt/cli.ini` and the invoking user's
`$XDG_CONFIG_HOME/letsencrypt/cli.ini` (or `$HOME/.config/letsencrypt/cli.ini`
when XDG is unset). This includes false
values, leading `--` forms, and underscore spellings: an explicit production
`--server` alone does not neutralize inherited testing mode, and a false alias
in another file is not a reliable override. Remove these mode directives from
global defaults and select the environment through `issue` instead. The helper
never edits those files or prints their settings.

Other global defaults remain available, but active settings must use ASCII
text with LF or CRLF line endings so alternative Unicode whitespace or bare
carriage-return separators cannot bypass the guard. Configuration
files must be readable regular files, not symbolic links. Because Certbot runs
here as root and executes any `pre-hook`, `post-hook`, or `deploy-hook` a
default file names, each present `cli.ini` and every directory above it must be
owned by root and not writable by group or other; a file another account could
edit or replace is not a trusted default, whatever it currently says. That
ownership check runs before the file's content is read. `HOME` (when XDG is
unset) and any explicit `XDG_CONFIG_HOME` must identify absolute paths without
glob characters; an empty explicit XDG value is rejected, and an XDG directory
under another account's home fails the ownership check. The official classic
Snap preserves the invoking user's home directory and uses the same sources.
The helper explicitly pins config, work, and log directories to
`/etc/letsencrypt`, `/var/lib/letsencrypt`, and `/var/log/letsencrypt`, keeping
issuance and renewal aligned with its existing-lineage guard and installed
units. A deployment needing other directories requires a separately reviewed
workflow. `--plan` describes these checks without reading host configuration;
apply rechecks the files before each Certbot invocation.

`--staging-passed` is an explicit operator assertion that the production names
match the successful test. The helper rejects wildcard names because they
require a deployment-specific DNS-01 plugin and protected provider credentials.

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
sudo certbot renew --dry-run \
    --server https://acme-staging-v02.api.letsencrypt.org/directory
sudo systemctl start certbot.service
sudo systemctl list-timers --no-pager | grep -Ei 'certbot|letsencrypt'
sudo /usr/local/bin/certbot-healthcheck
sudo systemctl start certbot-healthcheck.service
```

For the Snap backend, start `snap.certbot.renew.service` instead of
`certbot.service`. See [`../systemd/README.md`](../systemd/README.md) for the
complete backend contract, sandbox rationale, and runtime checks.

The daily health service inspects each lineage directory present in the live
tree. It exits nonzero when that tree is missing or empty, a lineage's
`fullchain.pem` is missing, unreadable, or unparseable, or a leaf certificate is
within 30 days of expiry. Valid Certbot links into the archive are supported;
dangling links are failures. Detecting a lineage whose entire directory was
removed requires a separately maintained inventory or endpoint check.

This is a local file and expiration check. It does not establish chain trust,
hostname matching, the start of a certificate's validity period, private-key
pairing, or which certificate nginx currently serves. Keep this service's
network confinement and add a separate deployment-owned HTTPS check using the
intended SNI/hostname, trusted chain, and expiry threshold. After a renewal or
reload, verify a fresh TLS connection reaches the expected certificate; a
successful reload command alone does not prove nginx adopted it.

The units intentionally contain no email, webhook, or vendor-specific
notification credential. Private host monitoring must cover failed renewal
and healthcheck units, missing or overdue scheduled runs, and the served HTTPS
endpoint. Add those checks and protected notification settings through the
deployment's Monit fragments or existing monitoring system, then verify both
a controlled failure notification and its recovery. Keep service restart
ownership with systemd.

### Reports and freshness

`certbot-healthcheck --format json` includes `checked_at_epoch`, `complete`,
and `healthy`. A complete inspection can be unhealthy because a certificate
is near expiry. An incomplete inspection reports `complete: false`,
`healthy: false`, empty certificate results, and unknown (`null`) counts.

Prometheus reports include `certbot_healthcheck_complete`,
`certbot_healthcheck_success`, and `certbot_healthcheck_timestamp_seconds`.
Each inspected certificate has an absolute
`certbot_certificate_expiry_timestamp_seconds` metric. The existing
`certbot_certificate_expiry_seconds` metric remains available as the remaining
time **at observation**, not a clock that updates between runs.

With `--output`, an inspection failure atomically replaces the previous report
with failure status and removes its certificate results. Argument errors,
failure to obtain the observation time, process termination, or an unwritable
output destination can leave an older file in place. Consumers must alert on
nonzero command status, missing or incomplete reports, unhealthy status, and
an observation timestamp older than their allowed collection interval. File
existence or a previously successful value alone is insufficient. Keep the
freshness allowance consistent with the daily timer's jitter and the host's
expected downtime.

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
- [Certbot global configuration](https://eff-certbot.readthedocs.io/en/stable/using.html#configuration-file)
- [Certbot testing-mode post-processing](https://github.com/certbot/certbot/blob/v5.7.0/certbot/src/certbot/_internal/cli/cli_utils.py)
- [Classic Snap environment behavior](https://snapcraft.io/docs/reference/development/environment-variables/#home)

## Parser regression check

`tests/certbot-issue` checks the portable helper and its preflight rejection
paths. `python3 tests/certbot-parser.py` additionally exercises the **actual
installed Certbot parser**, using only temporary configuration and webroot
directories. It never calls a Certbot command handler, certificate authority,
host service, or deployment hook. Run it in an isolated Python environment
with Certbot and the matching `acme` version installed. Keep this separate from
live issuance and repeat it when upgrading the selected Certbot package or
Snap; EPEL 9 and 10 can carry different Certbot generations.
