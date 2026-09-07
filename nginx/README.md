# nginx configuration

The deployable nginx tree. Platform requirements, the compatibility baseline,
and the full site configuration contract live in the [root README](../README.md);
this file covers only what someone editing this directory needs first.

## Layout

| Path | Purpose |
| --- | --- |
| `nginx.conf` | Top-level configuration; loads every matching stub, upstream, and site file present on the host. |
| `includes/` | Reusable `server`/`location` behavior included by site definitions. |
| `stubs/*.conf` | Installer-selected main-context fragments (module loaders, `quic_bpf`). |
| `stubs/http/*.conf` | Installer-selected `http {}` policies, maps, cache zones, and rate-limit zones. |
| `sites/` | Tracked defaults and inert public examples; deployment-local site definitions are ignored by Git. |
| `upstreams/` | Deployment-local upstream definitions; ignored by Git. |
| `trusted-proxies/` | Deployment-local `set_real_ip_from` directives; ignored by Git. |

Stubs are copied per host by `deploy/install-nginx` profiles. Never assume a
stub is present: a site or include that depends on one must be listed against
it in the root README's stub-dependency table and covered by a profile.

## Invariants

- `nginx.conf` loads `stubs/`, `stubs/http/`, `upstreams/`, and `sites/` by
  wildcard. Anything matching `*.conf` in those directories is live
  configuration on that host.
- `includes/security-headers.conf` sets `add_header_inherit merge`, so a
  `server` or `location` adding its own header keeps the inherited baseline.
  Replacing the baseline at a scope requires `add_header_inherit off` plus
  every required header restated there.
- The security-header values come from the `$*_fallback` maps in
  `stubs/http/upstream-fallback.conf`; an application-supplied header wins and
  the edge stays silent.
- `includes/quiet-common-requests.conf` serves from the enclosing server's
  filesystem `root` and belongs only in root-based sites (the WordPress
  layout). A reverse-proxy site including it would serve nginx's default
  root for those paths instead of the application; proxy sites declare their
  own quieted locations around `proxy_pass`.
- `includes/block-php.conf`, `includes/block-cgi.conf`,
  `includes/block-wordpress-probes.conf`, and
  `includes/block-project-files.conf` are server-context opt-ins that return
  nginx's non-standard `444` without access logging. Use runtime guards only
  on sites that do not expose those runtimes, the WordPress guard only on
  non-WordPress sites, and the project-file guard only where build metadata is
  never intentionally public. The project-file guard is defense in depth for
  OWASP's [guidance on old, backup, and unreferenced files](https://owasp.org/www-project-web-security-testing-guide/v42/4-Web_Application_Security_Testing/02-Configuration_and_Deployment_Management_Testing/04-Review_Old_Backup_and_Unreferenced_Files_for_Sensitive_Information),
  not a substitute for keeping those artifacts outside the web root. None of
  these opt-ins belongs in the universal security baseline.
- `sites/_http_.conf` and `sites/_https_.conf` are the default servers for
  unknown host names (444, and 421 with `ssl_reject_handshake`). Site files
  must declare their own `server_name` and must not claim `default_server`.
- TLS policy is shared in `stubs/http/tls.conf`. `ssl_protocols` is selected
  before SNI routing, so protocol changes belong there or on the listener's
  default server, never only on a non-default site. Hybrid post-quantum group
  selection is isolated in the optional `stubs/http/post-quantum.conf` profile.
- PHP-FPM integration is by tag: define `$site_tag`, provide the socket at
  `/run/$site_tag/php-fpm.sock`, and include the `*-by-tag.conf` file. Use
  `php-fpm-path-info-by-tag.conf` only for a narrowly scoped location that
  genuinely requires PATH_INFO.
- `wordpress-by-tag.conf` is the safe uncached WordPress default. Page caching
  requires both `wordpress-cache-by-tag.conf` and the `wordpress-cache`
  profile, and is an application-specific opt-in after cookie, authorization,
  query-parameter, personalization, commerce, and consent testing.
- `maintenance.conf` exposes its document only to an internal `error_page`
  redirect. If the optional file is absent, the route returns 503 so an
  upstream outage never becomes a false 404.
- WordPress static assets use a conservative 30-day generic lifetime. Successful
  asset and `robots.txt` requests remain quiet, while their 4xx and 5xx responses
  use the dedicated privacy-minimized `static-asset-failures.log`.
- Shared proxy includes treat this nginx instance as the public edge. They
  set `Host` and `X-Forwarded-Host` to `$host` and overwrite `X-Real-IP` and
  `X-Forwarded-For` with `$remote_addr`; never append
  an untrusted incoming forwarding chain, and strip the legacy `Proxy` request
  header before it reaches an application environment. The trusted-proxy
  profile may update `$remote_addr` only from explicitly trusted immediate
  peers. Both the ordinary HTTP and WebSocket variants replace a supplied
  `X-Request-Id` with nginx's `$request_id` for access-log correlation.
- `includes/relativeurls.conf` is a legacy opt-in, not shared WordPress policy.
  Do not enable response-body URL rewriting on an SEO-indexed site instead of
  generating correct absolute canonical and alternate-language URLs.

CI starts the rendered safe profile with the actual Node sample connected to
an isolated Unix-socket echo fixture. Its checks cover HTTPS redirection,
replacement of untrusted proxy, host, address and request-id headers, inherited
security headers, application CSP and cache-policy preservation, direct static
responses, and missing-static-file fallback to the application. Stopping the
fixture checks maintenance responses with and without the optional file and
denies direct access to that internal document. JSON access-log assertions
correlate the upstream request id, parse escaped values, and check that query
and Referer canaries are omitted. The separate WordPress checks retain
failure-only static asset logging coverage.
The runtime fixture uses foreground nginx with `UMask=0027` and a root-owned
`root:nginx 0750` logs directory. It verifies process masks, worker log-file
reopening after USR1, continued writes to the current log only, missing-file
recreation at `0640`, and mask retention across HUP. The container does not run
the full systemd sandbox or enforcing SELinux.

## Host runtime setup

After a reviewed render has been assembled at `/etc/nginx` and the exact live
tree passes `nginx -t`, apply its surrounding host settings with:

```sh
# Illustrative reviewed sizing, not automatic tuning:
nginx/setup --plan --workers 4 --threads-per-worker 32 --tasks-budget 512
sudo nginx/setup --workers 4 --threads-per-worker 32 --tasks-budget 512
```

The setup entry point verifies the installed nginx binary and live
configuration before changing anything, then applies the repository SELinux
registrations, additive firewalld service, systemd unit, logrotate policy,
QUIC sysctl limits, and host verification tools. Add `--quic-bpf` only when
the rendered nginx profile selected that feature. It never copies, renders,
or deletes anything under `/etc/nginx`; the reviewed tree remains a separate
deployment transaction.

The service keeps its root master in the foreground (`Type=exec`, `daemon off`)
to preserve the configured `0027` mask, with primary group `nginx` so workers
can traverse `root:nginx 0750` logs. Before restarting, setup changes only the
groups of existing root-owned runtime/state/log parent directories. This
prevents systemd's ownership migration from recursively changing existing
cache files and rotated logs. Unexpected ownership, symbolic links, or
group/other-writable parents fail preflight. The manual migration is documented
in [systemd](../systemd/README.md); do not replace the unit alone on an existing
installation. The service supplies `daemon off` itself, so the nginx tree must
not duplicate a `daemon` directive.

The root UID comes from the system manager's default, with explicit
`Group=nginx` and `SupplementaryGroups=nginx`. This removes inherited root
supplementary-group access and avoids systemd 257 dropping `CAP_SETUID` when
an explicit `User=root` is combined with seccomp restrictions. The checker
requires the exact master capability set and nginx-only supplementary group;
see the [execution contract](../systemd/README.md) for the source and details.

The ordinary master uses an explicit capability allowlist and denies mount
syscalls. `--quic-bpf` additionally installs the reviewed BPF capability
extension while preserving that mount denial. An ordinary apply retires only
the unchanged repository-owned BPF extension; it refuses a locally modified
file. Confirm the selected profile and validating kernel/nginx/SELinux behavior
before applying either contract.

The three sizing values must match the selected configuration and available
host/ancestor task capacity. They do not change nginx worker or pool settings.
`worker_processes auto` requires reviewing the target host's CPU count; include
all configured pools in the total threads per worker. Setup reserves two
worker generations plus 16 helper tasks and retains the shared `TasksMax=512`
ceiling. Review worker/pool sizing if the calculated requirement exceeds the
available budget. Overlapping more than one graceful reload needs additional
headroom or operational serialization; this check is not a runtime guarantee.

Applying host setup performs a planned restart to activate the installed
systemd execution restrictions and can interrupt service. `ExecStartPost`
performs bounded readiness/security checks on every start; setup additionally
checks the composed mount filter before restarting, then a fresh master and the
reviewed worker count. Schedule the operation and retain a recovery
plan. Configuration-only updates still use nginx's validated reload path.

`sites/sample_wp.conf.example` matches the `sample_wp` PHP-FPM pool and systemd
instance. Replace its domains, certificate paths, and site tag, then install it
as `sites/SITE_TAG.conf`; only the installed `*.conf` copy becomes active.
`sites/sample_node.conf.example` provides the corresponding reference for
Node.js reverse-proxied applications with static asset cache fallbacks.

## Validation

Render a profile and syntax-check it exactly as CI does, or run
`sudo nginx -t -c /etc/nginx/nginx.conf` on the target host. CI exercises
`nginx -t` for every push against the pinned stable and mainline nginx.org
packages on Rocky Linux 9, plus the latest stable package as a drift check.
It activates both `sample_wp.conf.example` and `sample_node.conf.example` only
in the ephemeral CI tree and generates a one-day self-signed certificate. The
Node sample receives a separate reserved domain while retaining its socket,
locations and shared includes. A Node 22 fixture serves that Unix socket; it
opens no IP listener. CI starts the safe profile for response-level policy
checks, then replaces the WordPress routing include with the cached variant
and parses that configuration too. Fixture behavior can also be checked with
`node --test tests/nginx-node-echo.test.mjs`; the full `tests/nginx-runtime`
command requires the prepared, disposable Linux CI container. A change that
adds a directive must stay within the root README's syntax floor or advance
it deliberately.

`tests/nginx-runtime-verifier` covers the actual read-only checker with isolated
process-status fixtures and a fixture monotonic clock, including startup delay,
window expiry, stalled and recovering manager queries, the `--startup` gate's
single stale-metadata warning, unexpected masks or capabilities, missing
workers, and log-directory traversal denial. It also rejects cleared, partial,
or allow-list replacements of the composed mount filter while the fixture
retains QUIC capabilities and `Seccomp: 2`.
`tests/nginx-setup` covers the setup flow with privileged commands mocked,
including preservation of existing cache descendants during parent-group
migration. Neither substitutes for a booted Linux unit test. On a target host,
run `/usr/local/libexec/nginx-runtime-verify --workers REVIEWED_COUNT` (add
`--quic-bpf` only for that profile), inspect `systemctl show`'s capability and
syscall policies, and verify application traffic, actual log rotation, reload,
and graceful shutdown. Review `systemd-analyze security` as an advisory report.
After changing execution policy, restart nginx before accepting runtime
verification. A standalone check reads the current composed policy; it cannot
prove that an unchanged master adopted filters from a later daemon-reload.

The runtime checker compares PID 1's expanded `SystemCallFilter` against the
target's own `systemd-analyze syscall-filter @mount` group. Every member must
remain denied, and `SystemCallErrorNumber=EPERM` must remain set. This gate
applies to ordinary and QUIC BPF profiles; other seccomp restrictions do not
substitute for mount denial. `--policy-only` checks the composed policy without
requiring a running master. A manager query that misses its deadline is retried
within the run's monotonic window, then fails closed; stale manager metadata
fails closed everywhere except the unit's own `--startup` gate, which logs a
warning because the loaded policy is what the new master received (see the
[systemd runtime contract](../systemd/README.md)). The installed host verifier
invokes the same check without `--startup`. The helper uses `systemctl`,
`systemd-analyze`, and coreutils `timeout`, with five-second command deadlines
and one additional second before forced cleanup.
