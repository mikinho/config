# Deployment tooling

`install-nginx` renders a complete nginx install tree from the repository
sources and the selected feature profiles. It only ever writes a new staging
directory — never the live `/etc/nginx` — so the rendered result can be
reviewed and installed deliberately.

Reusable application deployment policy lives under
[`application/`](application/README.md). It defines the versioned gold standard
for restricted transfer, immutable release activation, rollback, recovery, and
audit boundaries. Application projects vendor rendered bundles from that
standard; the nginx profile renderer remains the authority for their shared
edge configuration.

## Usage

```sh
deploy/install-nginx --check
deploy/install-nginx --list-profiles
deploy/install-nginx --verify-host
deploy/install-nginx --output /tmp/nginx-install \
    --profile gzip \
    --profile websocket
```

Because a render may happen on a build machine, the nginx version floor is
enforced where the tree actually loads: run `--verify-host` on the target
host before installing a render. It checks the exact `/usr/sbin/nginx` binary
used by the supplied systemd unit, fails when it predates 1.29.3
(`add_header_inherit`), and verifies the required compile-time modules listed
in the root README. Each render records the same floor as an
`nginx-version-floor` line in its `INSTALL-PROFILE` manifest.

`--verify-host` does not parse a rendered or live configuration and cannot
prove that optional dynamic modules or a selected TLS group load. The final
`/usr/sbin/nginx -t` against the exact installed tree remains mandatory.

The `baseline` profile is always selected. The output directory must not
exist; refusing to reuse one guarantees stale or deselected stubs cannot
survive from an earlier render. Each render writes an `INSTALL-PROFILE`
manifest recording the selected profiles and stubs, and generates a private
`quic_host.key` when the QUIC stub is selected.

Both renderers reject a final `.` or `..` path component, refuse symbolic-link
template inputs, recheck the output before publication, and verify that the
manifest landed at the requested root. These are build-output boundaries, not
permission to render an unreviewed checkout; review repository changes before
executing its tooling.

Install the reviewed result over the package-provided `/etc/nginx` using these
ownership boundaries:

- fully replace the repository-managed `nginx.conf`, `includes/`, and `stubs/`
  paths, deleting files absent from the new render;
- preserve package files such as `mime.types` and `fastcgi_params`;
- preserve deployment-local `sites/`, `upstreams/`, and `trusted-proxies/`;
- preserve an existing `/etc/nginx/quic_host.key`; copy the rendered key only
  for a host's initial installation; and
- take a recoverable backup, validate an assembled candidate, install it, run
  `/usr/sbin/nginx -t` again, and reload only after that exact test passes.

If using `rsync`, apply `--delete` only to the exact managed `includes/` and
`stubs/` directories, never to `/etc/nginx` as a whole. This prevents stale
fragments without deleting package, certificate, or deployment-local state.

## Standard host setup

`setup-host` composes the supported-OS component installers with their
repository setup policy. It supports two closed host profiles:

| Profile | Client path | Fail2ban policy |
| --- | --- | --- |
| `edge-direct` | Clients connect directly to nginx. | Enables SSH and nginx jails. |
| `edge-proxied` | A CDN or external load balancer is nginx's immediate peer. | Enables only SSH; firewall bans cannot act on restored client addresses. |

The profile is intentionally small: site configuration, trusted proxy CIDRs,
PHP pools, application services, and administrative ignore CIDRs remain
deployment state. The selected firewalld zone must already exist. Setup adds
the repository services but never installs `firewalld/zones/public.xml` or
removes unrelated allowances.

The exact reviewed nginx tree and supported `/usr/sbin/nginx` binary must be
installed first. Preview a complete first-pass plan with:

```sh
deploy/setup-host \
    --plan \
    --profile edge-direct \
    --ssh-phase prepare \
    --authorized-key-ready \
    --ignore-ip 192.0.2.0/24
```

Repeat `--ignore-ip` for each trusted administrative IPv4 or IPv6 CIDR. Add
`--certbot-backend snap` only for the official Snap payload, `--zone NAME` for
a non-default existing firewalld zone, and `--quic-bpf` only when the reviewed
nginx render selected that profile.

Apply preparation as root while retaining the existing SSH session:

```sh
sudo deploy/setup-host \
    --profile edge-direct \
    --ssh-phase prepare \
    --authorized-key-ready \
    --ignore-ip 192.0.2.0/24
```

Preparation keeps both SSH ports 22 and 2356 open and configures Fail2ban to
watch both. Prove a new key-authenticated, non-root login on port 2356. From
that new session, preview and apply finalization:

```sh
deploy/setup-host \
    --plan \
    --profile edge-direct \
    --ssh-phase finalize \
    --ignore-ip 192.0.2.0/24

sudo deploy/setup-host \
    --profile edge-direct \
    --ssh-phase finalize \
    --ignore-ip 192.0.2.0/24
```

Finalize refuses an SSH session not terminating on port 2356 unless
`--console-confirmed` explicitly records out-of-band recovery access. It
removes the temporary port-22 listener and allowances, updates Fail2ban to
2356, and runs `verify-deployment`. Use `--ssh-phase none` only for an already
finalized host; it leaves SSH untouched but still applies the other
components and verifies the result.

Each component remains independently callable through its `install` and
`setup` entry points. The orchestrator is rerunnable and each setup validates
before activation, but it is not one cross-component rollback transaction.
Always inspect the plan and retain recoverable host backups.

## PHP site renderer

`install-php-site` applies the same render-only philosophy to the per-site
PHP-FPM configuration set: it validates the site tag, renders the FPM main
configuration, pool, and systemd writable-paths drop-in from the public
`sample_wp` example into a new directory laid out as the files install, and
refuses an existing output. It never touches the live system and verifies no
sample token survives the render. `php-fpm/README.md` documents the
provisioning steps around it.

```sh
deploy/install-php-site --output /tmp/php-example_wp --tag example_wp
```

## Post-deployment verification

Install the repository-owned host tools into their fixed, trusted production
paths before application verification delegates to them:

```sh
deploy/install-host-tools --check
sudo deploy/install-host-tools
```

`verify-deployment` performs a non-destructive, root-only audit of live host
state,
asserting Nginx version and configuration, active systemd units, OpenSSH port
and authentication restrictions, firewalld service definitions, SELinux
Enforcing mode and port labels, QUIC buffer limits, and per-site PHP-FPM socket
permissions.

```sh
# Auto-detect the installed Certbot backend (recommended):
sudo /usr/local/bin/verify-deployment

# Explicit override for operator diagnosis:
sudo /usr/local/bin/verify-deployment --backend snap

# With per-site PHP-FPM socket check:
sudo /usr/local/bin/verify-deployment --site example_wp --verbose
```

`certbot-healthcheck` fails closed when the live tree is missing, empty, or
contains an invalid certificate. JSON output requires `jq`; file output is
published atomically and refuses symbolic-link destinations.

## Profiles

Profiles are line-per-stub manifests in `profiles/`. The profile table and
per-stub dependency rules live in the root README. Constraints enforced by
`--check` and CI:

- every stub under `nginx/stubs/` must be assigned to at least one profile;
- profile entries must be repository-relative stub paths, not symlinks; and
- profile names are lowercase alphanumerics and hyphens.

Adding a feature therefore means adding the stub, assigning it to a profile,
and updating the root README's tables in the same change.

### Example: production web server

The production server that fronts the Node.js and WordPress sites renders
with:

```sh
deploy/install-nginx --output nginx-production \
    --profile gzip \
    --profile brotli \
    --profile quic-bpf \
    --profile websocket \
    --profile wordpress-cache
```

Selection rationale, so the host's choices stay written down:

- `gzip` + `brotli` — both compressors; the applications precompress their
  build outputs, so `gzip_static`/`brotli_static` serve siblings and the
  runtime compressors cover proxied HTML and legacy assets.
- `quic-bpf` — the host kernel supports the QUIC reuseport eBPF map.
- `wordpress-cache` — the cache zone is available only to audited sites that
  explicitly include `wordpress-cache-by-tag.conf`; ordinary WordPress sites
  use the uncached `wordpress-by-tag.conf` and need no cache profile.
- `websocket` — websocket-enabled applications proxy through
  `includes/proxy-websocket.conf`, which requires the profile's
  `$connection_upgrade` map. The map degrades to an empty `Connection` header
  for ordinary requests, so the application's upstream keepalive pool is
  unaffected.
- `trusted-proxy` not selected — clients connect directly, so
  `$remote_addr` is already the client. Select it only if a CDN or other
  proxy is ever placed in front, together with its `trusted-proxies/`
  entries.
- `post-quantum` not selected until the host's TLS provider passes the
  syntax test described under Validation.

## Validation

CI shellchecks the installers and their negative-path tests, validates both
standard host profiles and their safety gates, runs `--check`, exercises a
full render, and verifies rejection of existing or ambiguous outputs and
symbolic-link source files. It then renders a CI
profile and runs `nginx -t` against the pinned stable and mainline nginx.org
packages on Rocky Linux 9. CI parses both the uncached and explicitly cached
WordPress site variants. It also renders the documented production profile so
profile drift is visible. RHEL-family jobs validate the two-stage effective
sshd ports, firewalld definitions, SELinux assets, component plans, and
rendered direct/proxied Fail2ban policy. The Brotli modules, `quic_bpf`
kernel/SELinux path,
and OpenSSL 3.5 hybrid group cannot be fully exercised by the stock CI
environment, so `brotli`, `quic-bpf`, and `post-quantum` must be syntax- and
runtime-tested with the exact modules, TLS provider, kernel, and policy on the
target host.
