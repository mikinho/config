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

## Compression on privileged locations

Review HTTP response compression per application and privileged `location`
(account, administration, authenticated API, and token-bearing form routes).
A scanner detecting compression alone does not establish an exploitable
[BREACH vulnerability](https://www.breachattack.com/): the response must combine
attacker-controlled reflected input with a secret in the same compressed body,
and the attacker must be able to induce repeated victim requests and observe
response-size differences. Review public forms too when they contain secrets.

For each relevant location:

- Inspect actual response bodies, including validation and error responses,
  for reflected input alongside CSRF tokens or other secrets. Choose a verified
  application mitigation, such as framework-supported CSRF token masking, or
  disable response compression there. Token masking protects that token; assess
  other secrets in the response separately. Keep compression on public pages
  and static assets that do not expose this combination.
- Apply the decision in the existing location that ultimately serves the
  response, accounting for regex locations, `try_files`, and internal redirects
  to a front controller. Preserve its access controls and proxy/FastCGI routing;
  adding an otherwise empty privileged-path location can change request handling.
- To disable nginx compression there, set `gzip off;` and, when the `gzip`
  profile is selected, `gzip_static off;` to disable its precompressed-file
  serving too. With the `brotli` profile loaded, also set `brotli off;` and
  `brotli_static off;`; with the `zstd` profile loaded, also set `zstd off;`
  and `zstd_static off;`. If that same location already includes
  `stubs/api/zstd.conf` through the API wildcard, remove the opt-in there
  instead of appending a duplicate `zstd` directive; keep any other required
  API fragments. Do not add Brotli or zstd directives on hosts
  without those modules. Removing HTML from
  [`gzip_types`](https://nginx.org/en/docs/http/ngx_http_gzip_module.html#gzip_types)
  cannot exclude it while gzip is enabled. See the separate
  [gzip static](https://nginx.org/en/docs/http/ngx_http_gzip_static_module.html),
  [Brotli](https://github.com/google/ngx_brotli#configuration-directives), and
  [zstd](https://nginx-extras.getpagespeed.com/modules/zstd/) controls.
- Check application/upstream and CDN compression as well. After `nginx -t`,
  verify representative authenticated responses through the public endpoint
  while advertising supported encodings; when disabling compression, confirm
  the delivered response has no compressed `Content-Encoding`. Use real body
  responses rather than HEAD alone. Record the route-specific decision and
  validation in private deployment documentation, and revisit it when templates,
  tokens, routing, or compression layers change.

This review concerns HTTP response bodies. TLS
[`ssl_certificate_compression`](https://nginx.org/en/docs/http/ngx_http_ssl_module.html#ssl_certificate_compression)
compresses handshake certificates and is a separate feature.

## Optional transport and compression profiles

Select `brotli` to install both the main-context module loaders and the HTTP
compression policy. The dynamic modules must match the installed nginx build;
verify the exact assembled tree with `nginx -t` after package changes. Keep
`gzip` selected for clients that do not negotiate Brotli. `brotli_static on`
serves existing `.br` siblings; it does not generate compressed assets.

Select `zstd` to prefer zstd on API routes. The profile installs the module
loaders, an `http`-level policy that keeps `zstd off`, and `stubs/api/zstd.conf`,
which API locations pull in through `include stubs/api/*.conf;` as the Node
sample does. Existing sites must add that include to each intended API
location; selecting the profile alone does not enable zstd there. Locations
without it retain the selected gzip/Brotli policy, and a render without the
profile leaves the include empty. This is a route boundary: HTML returned by
an opted-in API location is also eligible because `text/html` is implicit.
Keep `gzip` and `brotli` selected for their fallbacks; `zstd` does not install
either profile automatically.

Level 3 is the configured dynamic-response baseline; the nginx module's
default is 1. The 256-byte floor applies only when `Content-Length` is known.
An unknown-length or chunked response bypasses that size check, but still
needs an eligible content type, status, and client encoding. Measure CPU,
latency, and transfer size with representative payloads before tuning the
level; benchmark results are not universal performance guarantees. Client
support depends on the browser/runtime version and build, so use the actual
`Accept-Encoding` request header rather than assuming support from a name.

The MIME lists include JSON, common JSON subtypes, NDJSON, and CSV in all
three encoders so those responses have matching fallbacks. The lists do not
include `text/event-stream`; review buffering and flush behavior separately
before compressing streaming routes. Keep `zstd_static` at its default `off`
until the asset pipeline produces `.zst` siblings. Then enable it only in
static locations that have those files, use `on` to negotiate client support,
and preserve the originals and fallback siblings. Loading the static module
does not enable it or create compressed files. Leave `zstd_dict_file` unset
for ordinary browser traffic.

### Compression module order

The effective main-context loader order is:

```nginx
# stubs/brotli.conf, included first
load_module modules/ngx_http_brotli_filter_module.so;
load_module modules/ngx_http_brotli_static_module.so;
# stubs/zstd.conf, included afterward
load_module modules/ngx_http_zstd_filter_module.so;
load_module modules/ngx_http_zstd_static_module.so;
```

This is already supplied by `include stubs/*.conf;` before the `http` block;
do not paste a second copy into `nginx.conf`. With the supported modules'
ordering metadata, response filters run in reverse registration order, so an
eligible, unencoded API response tries **zstd, then Brotli, then gzip**. The
first filter to encode it sets `Content-Encoding`; subsequent compressors
leave it alone. Preserve the relative loader filenames. Moving the `gzip`,
`brotli`, or `zstd` directives inside `http`, changing `--profile` argument
order, or reordering equally weighted `Accept-Encoding` tokens does not set
server preference. Module build metadata can affect registration order, so
repeat the GET matrix below after package or loader changes.

This priority describes dynamic filters. Static handlers can serve an
already-encoded sibling before those filters, and an upstream application
can also supply `Content-Encoding`. The shared proxy policy forwards the
client's `Accept-Encoding`; nginx does not transcode such upstream bodies to
zstd. If nginx should own compression, configure the application to return
uncompressed responses on those routes. Adding just one `proxy_set_header`
inside a location replaces the inherited header set, so preserve the complete
proxy header policy when making an nginx-side override.

`gzip_vary on` is set once in `nginx.conf` for gzip/Brotli, including when the
gzip profile is absent. GetPageSpeed zstd 0.2.2 and newer add
`Vary: Accept-Encoding` independently and avoid duplicating an existing value;
older zstd versions relied on core nginx's `gzip_vary` behavior.
Verify both compressed and identity variants through any shared cache/CDN;
upstream-encoded responses and intermediary cache behavior need their own
correct `Vary` handling.

### Compression verification on the target host

Install the nginx/module pair from one compatible package family. For the
GetPageSpeed zstd continuation, use 0.2.2 or newer, or a supported vendor build
with equivalent fixes: [0.2.0 fixed `q=0` negotiation and static gzip fallback](https://github.com/GetPageSpeed/zstd-nginx-module/releases/tag/0.2.0),
and [0.2.1 fixed dynamic linking to libzstd](https://github.com/GetPageSpeed/zstd-nginx-module/releases/tag/0.2.1).
[0.2.2 fixes streaming truncation and emits its own Vary header](https://github.com/GetPageSpeed/zstd-nginx-module/releases/tag/0.2.2);
the earlier version floor does not include those integrity fixes.
The original tokers module and this continuation share directive names;
that does not establish equivalent behavior.

Public CI validates the zstd profile's rendered files. Its stock nginx
syntax/runtime job omits the third-party modules, so it does **not** run
`nginx -t` with zstd or establish its negotiation order. Check the assembled
target configuration and installed package versions before activation:

```sh
rpm -q nginx nginx-module-brotli nginx-module-zstd
sudo nginx -t
sudo nginx -T 2>&1 | grep -E '(^# configuration file .*stubs/(brotli|zstd)\.conf:|^[[:space:]]*load_module)'
```

Use a real **GET** to a stable, eligible API response over 256 bytes with
status 200 and `Content-Type: application/json`. HEAD may expose encoding
metadata but carries no compressed body, so `curl -I` cannot establish body
integrity or substitute for GET. Check that the intended config is
active and the application has not already encoded the response. Supply any
required authentication through your normal private test setup. With all
three profiles selected, expect:

| `Accept-Encoding` | Expected `Content-Encoding` |
| --- | --- |
| `zstd` | `zstd` |
| `zstd, br, gzip` | `zstd` |
| `gzip, br, zstd` | `zstd` |
| `br, gzip` | `br` |
| `gzip` | `gzip` |
| `zstd;q=0, br, gzip` | `br` |
| `zstd;q=0, br;q=0, gzip` | `gzip` |
| `identity` or no header | absent |

```sh
api_url='https://example.com/api/replace-with-an-existing-endpoint'
for encoding in 'zstd' 'zstd, br, gzip' 'gzip, br, zstd' 'br, gzip' 'gzip' \
    'zstd;q=0, br, gzip' 'zstd;q=0, br;q=0, gzip' 'identity'; do
    printf '\nAccept-Encoding: %s\n' "$encoding"
    curl --fail --silent --show-error --dump-header - --output /dev/null \
        --header "Accept-Encoding: $encoding" "$api_url"
done
# Explicitly omit the header for the last case.
curl --fail --silent --show-error --dump-header - --output /dev/null \
    --header 'Accept-Encoding:' "$api_url"
```

Inspect the status, `Content-Encoding`, and `Vary: Accept-Encoding` in every
result, including identity responses. These requests discard the encoded
body and do not require curl decoding support. Also verify a decoded GET
using `curl --compressed` with a build whose `curl --version` lists zstd, or
save the raw zstd response and decode it with `zstd -d` for content validation.
Do not treat a header alone as proof that the response body is intact.

Repeat for the API media types and statuses your application serves. Status
eligibility differs by module version: 0.2.2 expands the earlier 200/403/404
set to additional successful responses, while empty, partial, and server-error
responses can bypass it. Test a known-length response below 256
bytes, an unknown-length response, an excluded binary type, and a non-API
route as separate scope checks. Recheck the
[privileged-location compression review](#compression-on-privileged-locations)
when testing authenticated or token-bearing endpoints.

References: [GetPageSpeed configuration guide](https://www.getpagespeed.com/server-setup/nginx/nginx-zstd-compression),
[zstd module directives](https://nginx-extras.getpagespeed.com/modules/zstd/),
[Brotli filter ordering](https://github.com/google/ngx_brotli/blob/master/filter/config),
and [zstd filter ordering](https://github.com/GetPageSpeed/zstd-nginx-module/blob/0.2.2/filter/config).

### TLS and QUIC profiles

TLS certificate compression in `stubs/http/tls.conf` is independent of HTTP
response compression. The optional `post-quantum` profile selects hybrid TLS
groups when the linked TLS library supports them. Validate negotiation with a
capable client, since a successful syntax check alone does not prove either
feature is used.

The `quic-bpf` profile enables kernel routing of QUIC packets for connection
migration. It requires Linux 5.7 or newer, the matching systemd capability
extension, and the repository's SELinux BPF policy on enforcing hosts. Apply
the host setup with `--quic-bpf` and restart to activate the execution policy;
copying the nginx stub and reloading alone is insufficient. See the
[nginx HTTP/3 directives](https://nginx.org/en/docs/http/ngx_http_v3_module.html#quic_bpf)
for the feature's scope. Additional package modules, TLS early data, trusted
proxy handling, and application caching require their own use case and review;
a package subscription does not select those features.

When changing an installed profile selection, update `INSTALL-PROFILE` from a
matching `deploy/install-nginx` render together with the selected stubs. Preserve
the deployment's site definitions, upstreams, certificates, and QUIC key; a
profile change is not a replacement of the entire live tree.

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
worker generations plus 16 helper tasks and retains the shared `TasksMax=4096`
ceiling, which fits the shipped `aio threads` pool for up to 61 workers. Review
worker/pool sizing if the calculated requirement exceeds the available budget:
a ceiling that is too small surfaces as workers failing thread creation during
a reload, not as a clean refusal. `worker_shutdown_timeout 300s` in the shared
configuration asks each superseded generation to close its remaining
connections after five minutes; overlapping more than one graceful reload inside that
window still needs additional headroom or operational serialization. This
check is not a runtime guarantee.

When first adding or shortening `worker_shutdown_timeout`, plan a restart or
explicitly drain and verify the exit of every worker created under the previous
setting. HUP gives the timeout only to replacement workers; an existing
generation retains its previous value, including no timeout at all. Do not
assume waiting five minutes after that first reload clears the old generation.

Applying host setup performs a planned restart to activate the installed
systemd execution restrictions and can interrupt service. `ExecStartPost`
performs bounded readiness/security checks on every start; setup additionally
checks the composed mount filter before restarting, then a fresh master and the
reviewed worker count. Schedule the operation and retain a recovery
plan. Other configuration-only updates use nginx's validated reload path.

`sites/sample_wp.conf.example` matches the `sample_wp` PHP-FPM pool and systemd
instance. Replace its domains, certificate paths, and site tag, then install it
as `sites/SITE_TAG.conf`; only the installed `*.conf` copy becomes active.
`sites/sample_node.conf.example` provides the corresponding reference for
Node.js reverse-proxied applications with static asset cache fallbacks.

## Package upgrades

Before changing nginx packages or vendors, retain the installed configuration,
service unit and drop-ins, repository selection, and recovery packages. Keep
the intended stable or mainline channel explicit and install dynamic modules
that match the selected nginx build. Inspect package scripts as well as their
file payloads: a vendor's legacy binary-upgrade hook may fail under the managed
foreground service, leaving the old master running after the package
transaction succeeds. A configuration reload does not replace that binary;
plan a restart and verify the executable used by the new master.

After the package transaction and before stopping nginx, recheck the managed
runtime, lock, state, and log parent directories described above. RPM payloads
can reset a parent group, including `/var/log/nginx`, to `root`. For this unit,
restore only validated root-owned parent groups to `nginx` using the same
nonrecursive migration as host setup. Do not recursively change cache entries
or rotated logs. A mismatched parent can cause systemd directory setup to fail
before nginx's configuration test runs.

Review any `.rpmnew` files against the managed configuration, validate the
assembled tree with the installed binary, and confirm that selected optional
profiles still have their module, capability, and SELinux prerequisites. After
the planned restart, compare `/proc/$(systemctl show nginx -p MainPID --value)/exe`
with `/usr/sbin/nginx`, run the runtime checker with the selected BPF expectation,
and check application responses and compression negotiation. Package version
output and an active service alone do not establish that the new binary and
features are serving traffic.

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

The container rotation regression gives `CAP_SYS_PTRACE` only to its test
controller and removes it from nginx's bounding set before launch. An explicit
descriptor-access preflight distinguishes an unreadable `/proc/PID/fd` from
failed log reopening. The disposable systemd regression overrides all package
temporary paths within its managed state directory and uses a short timeout to
test both first adoption through reload and workers that inherited the timeout.

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
`systemd-analyze`, and coreutils `timeout`. Every command is limited to the
smaller of five seconds and the remaining overall window; forced cleanup can
take one additional second. Late query or process-inspection results cannot
pass verification. `--timeout-seconds 0` instead makes one attempt, with each
query independently limited to five seconds and no readiness retries.
