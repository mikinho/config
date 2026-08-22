# Secure nginx baseline

This repository provides a reusable, security-oriented nginx baseline for
RHEL-compatible hosts managed by systemd. Red Hat Enterprise Linux, CentOS
Stream, and Rocky Linux are the deployment authority; other distributions are
best-effort ports. The baseline supplies conservative HTTP and HTTPS defaults,
TLS, HTTP/2 and HTTP/3, compression, rate limiting, security-header fallbacks,
ACME handling, a local status endpoint, and optional integration points for
Node.js, PHP-FPM, and WordPress applications.

Site-specific virtual hosts may override the baseline where an application
has different requirements. Deployment-specific upstream addresses,
certificates, private keys, credentials, and other secrets do not belong in
this public repository.

This baseline assumes nginx is the edge server directly exposed to the public
internet. For traffic it proxies to an application, the edge must derive the
client identity from `$remote_addr` and overwrite client-supplied forwarding
headers; appending an untrusted `X-Forwarded-For` value permits IP spoofing.
The optional trusted-proxy profile changes where nginx obtains `$remote_addr`,
but only for explicitly trusted immediate peers.

## Compatibility baseline

| Component | Supported baseline | Reason |
| --- | --- | --- |
| nginx | **1.30.4 stable** or **1.31.3 mainline** from nginx.org; syntax floor **1.29.3** | `add_header_inherit` was introduced in 1.29.3. CI tests the listed RHEL 9 packages. |
| Linux kernel | A supported RHEL-family kernel; **5.7** for `quic_bpf` | The baseline uses `epoll` and systemd. The optional eBPF QUIC acceleration stub requires Linux 5.7 or newer. |
| OpenSSL | A vendor-supported version compatible with the selected nginx build; **3.5.1 or newer** for the `post-quantum` profile | The baseline uses the TLS provider's supported group defaults. The optional profile requires `X25519MLKEM768`. |
| systemd | **249** syntax floor; validated on Rocky Linux 9 and CentOS Stream 10 | Required for the service sandbox, including `ProtectProc` and `SocketBindDeny`. |
| PHP-FPM | **PHP 8.3 or newer** with systemd and POSIX ACL support; OPcache for production | Required only for the optional per-site PHP-FPM service and configuration under `php-fpm/`. |
| Certbot | A currently supported release | Required only for the included ACME renewal service and timer. |
| logrotate | A currently supported release | Required only when installing the included nginx file-log rotation policy. |
| fail2ban | A currently supported EPEL release | Required only for the optional intrusion-ban policy in `fail2ban/`. |
| OpenSSH | A supported RHEL-family sshd with the stock `sshd_config.d` include | Required only for the `ssh/` drop-ins; RHEL 8-era sshd lacks the include and silently ignores them. |
| firewalld | A currently supported RHEL-family release | Required only for the `firewalld/` service definitions and reference zone. |
| SELinux | Enforcing mode with the RHEL `httpd` policy | Expected on the supported platforms; do not disable it to deploy this baseline. |

nginx 1.29.3 is the minimum version that can parse the complete configuration,
not a supported deployment target. The package versions above were available
from nginx.org's RHEL-compatible repositories on August 21, 2026; advance the
CI matrix and this table together as those package families change.

Do not judge a RHEL-family package's security status from its upstream version
string alone. Red Hat backports security fixes, so vendor errata, package
release fields, and lifecycle status remain authoritative. That security
policy does not replace the optional `post-quantum` profile's functional
OpenSSL 3.5.1 floor: when that profile is selected, the TLS provider used by
nginx must expose `X25519MLKEM768` or `nginx -t` will fail.

NGINX Plus is not required. The configuration works with an appropriately
built nginx Open Source binary and does not rely on Plus-only directives.

## Required nginx build features

Check the installed binary with:

```sh
nginx -V 2>&1
```

The build must provide these non-default modules:

- `--with-threads`
- `--with-http_ssl_module`
- `--with-http_v2_module`
- `--with-http_v3_module`
- `--with-http_gzip_static_module`
- `--with-http_realip_module` when `stubs/http/realip.conf` is selected
- `--with-http_stub_status_module`
- `--with-http_sub_module` when `includes/relativeurls.conf` is used

The normally built HTTP proxy, FastCGI, cache, map, geo, rewrite, access,
gzip, and limit-request modules must not have been disabled. Building nginx
requires the corresponding OpenSSL, zlib, and PCRE2 (or compatible PCRE)
development libraries.

When Brotli is selected, the following dynamic modules must also be installed
under nginx's module directory:

```text
ngx_http_brotli_filter_module.so
ngx_http_brotli_static_module.so
```

They must be built for the exact nginx binary and compatible configure
arguments in use. A module copied from a different nginx build may fail to
load even when the version number appears similar.

[GetPageSpeed NGINX Extras](https://www.getpagespeed.com/) is an intended
RHEL-family package source for deployments that need pre-built dynamic-module
RPMs. Its `nginx-module-brotli` package provides both modules named above.
Keep nginx and every dynamic module as one publisher-supported,
binary-compatible package set; do not leave EPEL, nginx.org, or GetPageSpeed
modules from a different nginx build installed alongside it.

## Host prerequisites

Use the official nginx.org repositories for RHEL and derivatives unless a
reviewed vendor package supplies the required version and build options. The
distribution package may lag below this configuration's syntax floor or omit
HTTP/3. Install runtime dependencies with `dnf`, keep the host within its
vendor lifecycle, and apply the vendor's current security errata.

Several deployments use GetPageSpeed's subscription repository for nginx,
NGINX-MOD, and matching module RPMs. Those packages are not downloaded by this
public repository's CI, so validate their actual `nginx -V`, OpenSSL provider,
module set, and `nginx -t` result on every target host and after every package
upgrade. The nginx.org RPMs remain the reproducible public CI reference, not a
requirement to replace a validated GetPageSpeed installation.

The checked-in paths assume this layout:

| Path or resource | Requirement |
| --- | --- |
| `/etc/nginx` | Deployment root for this repository's nginx files. |
| `/etc/nginx/mime.types` | nginx MIME type database, normally supplied by the nginx package. |
| `/etc/nginx/fastcgi_params` | FastCGI parameter file, required by the PHP-FPM includes. |
| `/etc/nginx/quic_host.key` | Private persistent HTTP/3 token key generated by the profile installer; mode `0600`. |
| `/usr/sbin/nginx` | nginx binary used by `systemd/nginx.service`. Adjust the unit if the package installs it elsewhere. |
| `/bin/certbot` | Certbot binary used by `systemd/certbot.service`. Adjust the unit if needed. |
| `/bin/systemctl` | systemctl binary used by the Certbot post-renewal reload. |
| `/sbin/php-fpm` | Administrator-managed, version-neutral PHP-FPM binary contract used by `systemd/php-fpm@.service`. |
| `nginx` user and group | Worker identity configured by `nginx/nginx.conf`. |
| `/run/nginx` | PID directory; created by the nginx unit. |
| `/run/lock/nginx` | Lock directory; created by the nginx unit. |
| `/var/log/nginx` | nginx log directory; created by the nginx unit. |
| `/var/lib/nginx/client_tmp` | Writable client-body temporary directory. |
| `/var/lib/nginx/fastcgi_tmp` | Writable FastCGI temporary directory. |
| `/var/lib/nginx/proxy_tmp` | Writable proxy temporary directory. |
| `/var/lib/nginx/fastcgi` | Writable FastCGI cache directory when PHP or WordPress caching is enabled. |
| `/var/www/letsencrypt` | Root-owned ACME HTTP-01 webroot; mode `0750` with group `nginx` is recommended so Certbot can write and nginx can read. |
| `/etc/nginx/trusted-proxies` | Deployment-local `set_real_ip_from` directives when trusted proxy address restoration is enabled. |

The host firewall must allow TCP ports 80 and 443. Allow UDP port 443 as well
to make HTTP/3 available; clients fall back to HTTP/2 or HTTP/1.1 when UDP is
unavailable. Install `sysctl/99-nginx-quic.conf` so the kernel honors the
QUIC listeners' requested socket buffers instead of silently capping them.

Keep SELinux enforcing. Restore the distribution labels after installing the
configuration and content, and use `matchpathcon` or `ausearch` to investigate
any denial instead of weakening the policy globally. Non-standard content
roots need a persistent `semanage fcontext` rule followed by `restorecon`.
When nginx must proxy to a TCP upstream, review and enable the standard
`httpd_can_network_connect` boolean; a Unix-domain socket or a tighter local
policy is preferable when practical. The `policycoreutils` and
`policycoreutils-python-utils` packages provide the relevant administration
tools on RHEL-family systems. The `selinux/` directory supplies the policy
registrations this baseline needs beyond the distribution policy — runtime
file contexts, the QUIC UDP port label, the setrlimit boolean — and the
optional `quic_bpf` policy module.

nginx still describes its HTTP/3 module as experimental. Validate it against
the deployed TLS library and clients, monitor QUIC-specific errors, and retain
TCP 443 so clients always have an HTTP/2 or HTTP/1.1 fallback.

## Deployment layout

- `nginx/nginx.conf` is the top-level configuration.
- `nginx/includes/` contains reusable behavior included by site definitions.
- `nginx/stubs/*.conf` contains installer-selected main-context fragments such
  as dynamic module loaders and optional QUIC eBPF acceleration.
- `nginx/stubs/http/*.conf` contains installer-selected `http {}` policies,
  maps, cache zones, and rate-limit zones. `nginx.conf` loads every matching
  stub present on the deployed host, so the installer must copy only the
  features that host needs.
- `nginx/sites/` contains public-safe site configuration.
- `nginx/upstreams/` is reserved for deployment-specific upstream definitions.
  Its contents are ignored by Git, while its `.gitignore` keeps the empty
  directory in the repository.
- `nginx/trusted-proxies/` is reserved for deployment-specific trusted proxy
  CIDRs. Its contents are likewise ignored by Git.
- `systemd/` contains the nginx, per-site PHP-FPM, and Certbot renewal units.
- `php-fpm/` contains the optional per-site PHP-FPM pool, main configuration,
  provisioning contract, and validation guidance.
- `logrotate/` contains the nginx file-log rotation policy.
- `deploy/` contains the profile installer and its profile manifests.
- `selinux/` contains the SELinux file-context and policy-module assets that
  enforcing-mode deployments need beyond the distribution policy.
- `fail2ban/` contains an optional intrusion-ban policy for deployments that
  receive client traffic directly.
- `sysctl/` contains the kernel socket-buffer limits the QUIC listeners
  depend on.
- `ssh/` contains `sshd_config.d` drop-ins hardening the distribution
  OpenSSH daemon.
- `firewalld/` contains per-product firewall service definitions and a
  reference zone.

Each component directory carries its own README covering that component's
installation and validation; this document remains authoritative for the
platform baseline and the cross-component contracts.

Deploy the selected contents of `nginx/` to `/etc/nginx/`, preserving this
directory structure. The installer must populate both stub levels with the
features selected for that host; copying every source stub enables every
optional feature. Install the unit files in the host's system unit directory
only after reviewing their absolute paths for that distribution. Install
`logrotate/nginx` as `/etc/logrotate.d/nginx` only if the package has not
already installed an nginx rotation policy.

The profile installer renders a new, non-existing staging directory and never
writes directly to the live nginx configuration. The `baseline` profile is
always selected; add only the features the host needs:

```sh
deploy/install-nginx --check
deploy/install-nginx --list-profiles
deploy/install-nginx --output /tmp/nginx-install \
    --profile gzip \
    --profile websocket
```

Review the rendered `INSTALL-PROFILE` and configuration, then install it over
the package-provided `/etc/nginx`. Treat `includes/`, `stubs/`, and `nginx.conf`
as repository-managed: fully replace those paths so a deselected or removed
fragment cannot survive. Preserve package files such as `mime.types` and
`fastcgi_params`, and preserve deployment-local `sites`, `upstreams`, and
`trusted-proxies` content. Preserve an existing `/etc/nginx/quic_host.key`;
the generated key is for a host's initial install only. Take a recoverable
backup, assemble the exact candidate tree, run `/usr/sbin/nginx -t`, and only
then reload. The renderer refuses an existing output directory so stale or
unselected stubs cannot silently survive inside a render.

| Profile | Adds |
| --- | --- |
| `baseline` | Required privacy-minimized logging, rate-limit, security-header fallback, TLS, and persistent HTTP/3 key stubs. Always selected. |
| `gzip` | gzip response compression. |
| `brotli` | Paired Brotli module-loader and HTTP compression stubs. |
| `websocket` | WebSocket connection-upgrade map. |
| `wordpress-cache` | Opt-in WordPress FastCGI page-cache zone and conservative bypass maps. Ordinary WordPress routing needs no profile. |
| `trusted-proxy` | Client address restoration; deployment-local trusted CIDRs are still required. |
| `post-quantum` | Hybrid `X25519MLKEM768` TLS key exchange for compatible OpenSSL 3.5.1+ builds. |
| `quic-bpf` | Linux eBPF acceleration for QUIC connection migration after host validation. |

The selected configuration has these stub dependencies:

| Stub | Context | Install when |
| --- | --- | --- |
| `brotli.conf` | main | Brotli is selected; install together with `http/brotli.conf` and compatible dynamic modules. |
| `quic-bpf.conf` | main | Linux 5.7+ eBPF acceleration for QUIC connection migration has been validated on the host. |
| `http/quic.conf` | http | The provided HTTP/3 listener is installed. This is required by the `baseline` profile. |
| `http/tls.conf` | http | Any selected site listens with `ssl`, including `_https_.conf`. This is required for HTTPS deployments. |
| `http/post-quantum.conf` | http | nginx uses a TLS provider exposing `X25519MLKEM768`; select through the optional `post-quantum` profile. |
| `http/upstream-fallback.conf` | http | The baseline `security-headers.conf` include is enabled. This is required by the provided `nginx.conf`. |
| `http/logging.conf` | http | The privacy-minimized JSON access log is enabled. This is required by the provided `nginx.conf`. |
| `http/ratelimit.conf` | http | A selected site or include uses `limit_req` or `limit_conn`, including `_http_.conf`, `http.conf`, and `wordpress-by-tag.conf`. |
| `http/fastcgi-cache.conf` | http | A site includes `wordpress-cache-by-tag.conf`; select the `wordpress-cache` profile. |
| `http/websocket.conf` | http | A reverse-proxy site uses `$connection_upgrade`, including through `includes/proxy-websocket.conf`. |
| `http/realip.conf` | http | nginx receives traffic through explicitly trusted reverse proxies and rate limits must use the restored client address. |
| `http/gzip.conf` | http | gzip response compression is desired. |
| `http/brotli.conf` | http | Brotli response compression is desired; install together with `brotli.conf`. |

The installer generates `quic_host.key` with 80 bytes from the operating
system random source and mode `0600`; it never prints the key. This is initial
host state, not a rotation mechanism: subsequent installs must preserve the
existing `/etc/nginx/quic_host.key`. A manual deployment must create an
equivalently private persistent key before running `nginx -t`. The key is
operational secret state and must never be committed.

The TLS stub deliberately omits `ssl_stapling off` because stapling is already
disabled by default, and whether to enable it depends on the certificate
authority and deployment. It also leaves TLS group selection to the installed
provider. The optional `post-quantum` profile instead prefers the OpenSSL 3.5
hybrid `X25519MLKEM768` group, then retains X25519 and P-256 as classical
fallbacks. A site using an ECDSA certificate on another curve must include that
curve. `ssl_protocols` is selected before nginx can apply an SNI-selected
virtual host's configuration, so a deployment that overrides it must do so on
the listener's default server rather than only on a non-default site.

### nginx service sandbox

`systemd/nginx.service` makes the host filesystem read-only to nginx except
for its systemd-managed runtime, state, and log directories. It also hides
home directories, restricts process and kernel views, limits address families,
and denies writable-executable memory. A site that needs to write uploads or
generated content outside `/var/lib/nginx` must add only the required path with
a systemd `ReadWritePaths=` drop-in. A build that enables PCRE JIT or another
JIT-based dynamic module must override `MemoryDenyWriteExecute=` after review.

The unit does not restrict nginx's capability bounding set or system-call
allowlist. The privileged master must bind ports 80 and 443, change worker
identity, raise file limits, and signal workers. Selecting `quic_bpf` also
initializes an eBPF map and may require additional capabilities. Those controls
require host-specific validation against the installed kernel, selected stubs,
and nginx build. `LimitMEMLOCK=64M` is provided for the optional eBPF map.

## Site configuration contract

The shared configuration is a baseline, not an application policy engine.
Individual sites and upstream applications may set stricter or
application-specific behavior.

The default request-body limit is 16 MiB. Sites that need larger uploads should
raise `client_max_body_size` only at the narrowest applicable `server` or
`location`; sites that accept only small requests may lower it further.

### Trusted proxy client addresses

Direct clients can forge forwarding headers, so the baseline never trusts
them automatically. Selecting `stubs/http/realip.conf` enables recursive
`X-Forwarded-For` processing only for peers listed by `set_real_ip_from` in
deployment-local `trusted-proxies/*.conf` files. For example, a reverse proxy
on the same host can be declared with:

```nginx
set_real_ip_from 127.0.0.1;
set_real_ip_from ::1;
```

Use only the immediate proxy addresses or exact provider-published networks;
never use an unrestricted range. The restored address becomes `$remote_addr`
and `$binary_remote_addr`, so the shared rate limits apply per client instead
of treating every request as the proxy or exempt loopback. A provider-specific
header or the PROXY protocol requires a reviewed site-specific configuration,
matching listener settings, and controls that prevent direct origin access.

### Security headers

`includes/security-headers.conf` supplies fallback values for common security
headers. Upstream applications may provide their own values for CSP,
`X-Content-Type-Options`, `Referrer-Policy`, `X-Frame-Options`, and
`Permissions-Policy`; nginx uses the upstream value instead of duplicating it.
For proxied responses, nginx hides upstream HSTS and supplies the edge policy.
The shared PHP-FPM include likewise hides application-provided HSTS and
`X-Powered-By`; custom FastCGI configurations should do the same.

The baseline selects `add_header_inherit merge`, so headers defined by a
`server` or `location` are appended without discarding the inherited security
headers. A site that must replace the complete inherited policy can set
`add_header_inherit off` at that scope and then define every required header
explicitly. This escape hatch is intentionally conspicuous because it also
removes inherited HSTS.

The baseline HSTS policy deliberately omits `includeSubDomains` and `preload`.
Either directive commits more than the current hostname and is unsafe as a
universal default. An individual site may opt into `includeSubDomains`, and
later preload, only after its entire namespace and the preload requirements
have been verified. A site-level HSTS override must select
`add_header_inherit off` and restate the other required `add_header` directives
at that scope so the baseline and site-specific HSTS values are not both
emitted.

### Reverse-proxied applications

Proxy locations include `includes/proxy-common.conf`, then set `proxy_pass`
and any application-specific overrides. The include fixes HTTP/1.1 with an
empty `Connection` header, forwards `Host`, `X-Real-IP`, `X-Forwarded-For`,
`X-Forwarded-Proto`, and an edge-assigned `X-Request-Id` matching the access
log's `request_id` field, strips the legacy `Proxy` request header, and sets
conservative connect, send, and read timeouts. Stripping `Proxy` prevents a
CGI-derived upstream environment from mistaking untrusted request data for its
outbound `HTTP_PROXY` setting. An application that adopts the request id header
as its own can be correlated with the edge log line for the same request.
Upstream keepalive additionally requires a `keepalive` pool in the
deployment-local upstream block:

```nginx
upstream app {
    server unix:/run/app/node.sock;
    keepalive 16;
}
```

WebSocket endpoints include `includes/proxy-websocket.conf` instead — never
both in one location — which carries the same forwarding policy with
connection upgrading and a longer read timeout. It requires the `websocket`
profile's `$connection_upgrade` map.

This configuration is for an nginx edge server directly exposed to the public
internet. Both shared proxy includes therefore set `X-Real-IP` and
`X-Forwarded-For` to nginx's `$remote_addr`; they never append the incoming
`X-Forwarded-For` chain. Using `$proxy_add_x_forwarded_for` here would preserve
attacker-supplied addresses and could let an application authorize, rate-limit,
or audit the wrong client. When the trusted-proxy profile is selected, the real
IP module first updates `$remote_addr` from only an explicitly trusted peer, so
the same forwarding rule remains correct.

### Optional Node.js Cache-Control fallback

Some Node.js applications serve static files through nginx while generating
their other responses in the application. The shared configuration therefore
provides an opt-in `$cache_control_fallback` map.

The map is not loaded globally because its `$static_cache_control` input is
owned by the application. Loading it when no participating application is
installed would make `nginx -t` fail with an unknown-variable error.

Define the application policy at `http {}` scope, then include the fallback
map exactly once:

```nginx
map $uri $static_cache_control {
    "~*[._-][a-f0-9]{8,}\.[a-z0-9]+$" "public, max-age=31536000, immutable";
    default                              "public, max-age=3600";
}

include includes/cache-control-fallback.conf;
```

The application can then apply the result at the same configuration scope as
its other response headers:

```nginx
add_header Cache-Control $cache_control_fallback always;
```

For a response served by nginx, the application's static policy is emitted.
For a proxied response, the fallback is empty, allowing the Node.js
application's own `Cache-Control` header to pass through without duplication.

### Optional PHP-FPM and WordPress support

The PHP-FPM and WordPress includes are opt-in. A site using them must:

- define `$site_tag` before including a `*-by-tag.conf` file;
- provide a PHP-FPM socket at `/run/$site_tag/php-fpm.sock`;
- select `wordpress-by-tag.conf` for the safe, uncached routing default; or
- select `wordpress-cache-by-tag.conf` plus the `wordpress-cache` deployment
  profile only after reviewing the application's complete authentication,
  query-parameter, personalization, commerce, and consent behavior.

The corresponding per-site PHP-FPM implementation is documented in
[`php-fpm/README.md`](php-fpm/README.md). PHP-FPM owns each socket; no systemd
socket unit is used. The service runs as the site tag, grants nginx access with
a POSIX ACL, and confines writable state to explicitly provisioned paths.

The default WordPress PHP locations accept only requests whose URI ends in
`.php`; PATH_INFO routing broadens the executable request surface and is not a
universal default. An application that requires it must use a narrowly scoped
location and include `includes/php-fpm-path-info-by-tag.conf` instead of the
standard PHP-FPM include. The shared PHP-FPM includes forward HTTPS state to
the application so WordPress and other frameworks can generate secure URLs.

The WordPress upload policy rejects PHP variants, scripts, and active browser
content such as HTML, JavaScript, and SVG, including common double-extension
forms. A site that intentionally accepts one of those formats must replace
that location with an application-specific validation and serving policy.

The generic WordPress static route uses a 30-day browser lifetime because the
baseline cannot assume every theme, plugin, image, font, script, or stylesheet
gets a new URL when its content changes. A site whose release process guarantees
fingerprinted filenames or a changed version parameter may replace that policy
with a one-year immutable lifetime. Successful assets remain out of access
logs; 4xx and 5xx asset responses and a failing `robots.txt` are written to
`/var/log/nginx/static-asset-failures.log` using the same privacy-minimized JSON
format and rotation policy as the main access log.

The shared WordPress route handles upstream 502, 503, and 504 responses through
the optional document at the site's `/maintenance.html` filesystem path. That
URI is internal-only: clients cannot request or index the maintenance document
directly. If the file is absent, nginx returns 503 rather than converting an
application outage into a false 404. A deployment that provisions the document
should keep it static, free of tracking, and appropriate for every error in
that class.

The optional WordPress cache bypasses every request carrying any `Cookie`,
`Authorization` header, or query string, plus non-GET/HEAD methods and known
WordPress private routes. It also honors application `Cache-Control`,
`Expires`, `Set-Cookie`, and `Vary` response behavior. These are conservative
guards, not proof that a site is safe to cache: opt in only after testing every
personalized, logged-in, commerce, consent, and parameter-driven path. The
uncached include is the universal default.

`includes/relativeurls.conf` remains available only for narrowly reviewed
legacy applications. It rewrites HTML attributes in response bodies and can
change canonical or alternate-language URLs, so it is not included by the
shared WordPress path and should not be enabled on an SEO-indexed site in place
of correct application URL generation.

## Certbot renewal behavior

`systemd/certbot.timer` invokes `systemd/certbot.service`. The service always
attempts an nginx reload after Certbot exits, including when Certbot reports a
partial renewal failure. This is intentional: certificates that did renew
successfully must be loaded even when another certificate in the same run did
not renew. The reload runs from `ExecStopPost`, so Certbot's original nonzero
status is preserved rather than ignored. A failed nginx reload also causes the
unit to fail visibly.

The timer checks at midnight and noon in the host's local time with up to six
hours of randomized delay. Frequent checks are safe because `certbot renew`
acts only on certificates near expiry, while the randomized windows avoid
synchronized ACME traffic. Keep `Persistent=true` so a missed run is triggered
after the host returns.

The included nginx HTTP listener serves `/.well-known/acme-challenge/` from
`/var/www/letsencrypt`. Certificates and account data under `/etc/letsencrypt`
are host state and must never be committed.

The Certbot unit is confined to the webroot renewal flow used by this
repository. It can write only its systemd-managed configuration, state, and
log directories plus `/var/www/letsencrypt`; it cannot bind a listening
socket and has no Linux capabilities. Renewal hooks and authenticator plugins
inherit this sandbox. A deployment using the standalone authenticator, an HSM,
or a hook that writes elsewhere must add the smallest necessary systemd
drop-in and re-run the dry-run validation.

`SystemCallFilter` and `MemoryDenyWriteExecute` are intentionally not enabled
for Certbot until they have been tested with the target host's Python,
cryptography library, and installed plugins.

Many distributions install their own Certbot timer. Do not leave two renewal
schedulers active; choose either the distribution unit or this repository's
unit after comparing their behavior.

## Logging and privacy contract

The JSON access logs record the client IP address, authenticated username when
present, host, method, URL path, whether a query string existed, status,
response size, referrer without its query string, user agent, negotiated
protocol and TLS details, timings, and upstream cache status. The dedicated
static-asset failure log uses that same format but records only 4xx and 5xx
responses. Query-string values are deliberately not recorded from either the
request or referrer because applications and links sometimes place personal
data or credentials there. The included logrotate policy retains 14 rotations
by default; a deployment may reduce that period to meet its actual operational
need.

IP addresses, usernames, referrers, user agents, and paths can still be
personal data. Restrict journal and log-file access, document the actual
purpose, recipients/processors, retention, and user rights in the deployed
site's privacy materials, and do not reuse operational logs for analytics
without a separate review. This repository supplies technical minimization,
not a jurisdiction-specific legal determination.

## Production application release gate

Passing the infrastructure checks below does not certify a website's content,
WordPress installation, consent implementation, or SEO. Those artifacts are
deployment-local and intentionally absent from this public configuration
repository. Before moving public traffic, the application owner must verify:

- the published Privacy Policy accurately describes the site's real data
  collection, processors, purposes, retention, choices, and contact process,
  including the server logging described above;
- a clean browser session sets no nonessential cookies or storage before
  consent, rejecting optional categories keeps them absent, accepting enables
  only the selected categories, and Cookie Preferences can later revoke them;
- canonical and `hreflang` URLs are absolute and correct, the primary-host
  redirect is permanent, intended pages are indexable, and `robots.txt`, XML
  sitemaps, titles, descriptions, and structured data match the production
  hostname rather than a staging environment;
- WordPress core is on a vendor-supported security release, plugins and themes
  are patched and necessary, debug display is off, administrator access is
  least-privilege, and backups plus restoration have been tested; and
- PHP OPcache, the selected compression profiles, static cache headers, and
  application performance have been measured on the production build without
  weakening the security or consent behavior above.

Repeat the consent, caching, headers, redirects, and HTML checks after each
material application, plugin, theme, proxy, or CDN change.

## Validation

GitHub Actions validates deployment profile coverage, exercises the installer,
runs `nginx -t` against stable and mainline nginx.org packages on Rocky Linux
9, exercises security and failure behavior against a running nginx, checks the
units and logrotate policy on Rocky Linux 9 and CentOS Stream 10, and scans the
complete Git history for secrets. The weekly run also compares
the checksum-pinned Actionlint release with its current upstream release so a
manual binary pin cannot silently become stale. GitHub's Ubuntu hosted runner
is only the Docker and portable-tooling executor; it is not a supported
deployment target. The third-party Brotli modules, `quic_bpf` kernel/SELinux
path, and OpenSSL 3.5 hybrid group cannot be fully exercised by the stock CI
environment. The `brotli`, `quic-bpf`, and `post-quantum` profiles are therefore
profile-validated in public CI and must be syntax- and runtime-tested with the
exact modules, kernel, policy, and TLS provider on the target host.

Run these checks on the target host before enabling the service:

```sh
openssl version
# Required only when the post-quantum profile is selected:
openssl list -tls1_3 -tls-groups | grep X25519MLKEM768
deploy/install-nginx --verify-host
/usr/sbin/nginx -v
/usr/sbin/nginx -V 2>&1
sudo /usr/sbin/nginx -t -c /etc/nginx/nginx.conf
sudo systemd-analyze verify /etc/systemd/system/nginx.service
sudo systemd-analyze verify /etc/systemd/system/certbot.service /etc/systemd/system/certbot.timer
sudo systemd-analyze security nginx.service certbot.service
sudo certbot renew --dry-run
```

After deployment, verify the expected headers and protocol behavior against a
non-production hostname before moving public traffic. Be cautious with
`nginx -T`: its output may disclose hostnames, upstream addresses, filesystem
paths, and other deployment details unsuitable for a public issue or log.

## Public repository rules

Before committing, confirm that the diff contains none of the following:

- private keys, certificates, ACME account state, or credentials;
- real upstream IP addresses, ports, or internal DNS names;
- customer or production hostnames that are not already public; or
- generated logs, caches, PID files, temporary files, or local editor state.

The root, `nginx/upstreams/`, and `nginx/trusted-proxies/` `.gitignore` files
enforce the common cases, but they are not a substitute for reviewing the
staged diff.

This repository is available under the [MIT License](LICENSE).

## References

- [nginx HTTP/2 module](https://nginx.org/en/docs/http/ngx_http_v2_module.html)
- [nginx HTTP/3 module](https://nginx.org/en/docs/http/ngx_http_v3_module.html)
- [nginx current releases](https://nginx.org/en/download.html)
- [nginx packages for RHEL and derivatives](https://nginx.org/en/linux_packages.html#RHEL-CentOS)
- [GetPageSpeed NGINX modules for enterprise Linux](https://www.getpagespeed.com/)
- [GetPageSpeed Brotli RPM installation](https://www.getpagespeed.com/server-setup/nginx/install-nginx-with-brotli-module-in-centos-redhat-amzn-linux)
- [nginx header inheritance](https://nginx.org/en/docs/http/ngx_http_headers_module.html#add_header_inherit)
- [nginx real IP module](https://nginx.org/en/docs/http/ngx_http_realip_module.html)
- [nginx build options](https://nginx.org/en/docs/configure.html)
- [nginx SSL module](https://nginx.org/en/docs/http/ngx_http_ssl_module.html)
- [OpenSSL release strategy](https://openssl-library.org/policies/releasestrat/)
- [OpenSSL TLS group configuration](https://docs.openssl.org/3.5/man3/SSL_CONF_cmd/)
- [Red Hat security backporting policy](https://access.redhat.com/security/updates/backporting)
- [RHEL 9 SELinux guidance](https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/9/html/using_selinux/configuring-selinux-for-applications-and-services-with-non-standard-configurations_using-selinux)
- [ngx_brotli build instructions](https://github.com/google/ngx_brotli)
- [Certbot automated renewal](https://eff-certbot.readthedocs.io/en/stable/using.html#setting-up-automated-renewal)
