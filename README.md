# Secure nginx baseline

This repository provides a reusable, security-oriented nginx baseline for
Linux hosts managed by systemd. It supplies conservative HTTP and HTTPS
defaults, TLS, HTTP/2 and HTTP/3, compression, rate limiting, security-header
fallbacks, ACME handling, a local status endpoint, and optional integration
points for Node.js, PHP-FPM, and WordPress applications.

Site-specific virtual hosts may override the baseline where an application
has different requirements. Deployment-specific upstream addresses,
certificates, private keys, credentials, and other secrets do not belong in
this public repository.

## Compatibility baseline

| Component | Minimum | Reason |
| --- | --- | --- |
| nginx | **1.29.3** | `add_header_inherit` was introduced in 1.29.3 and preserves the shared security-header baseline across nested configuration scopes. |
| Linux kernel | **5.7** | Required by `quic_bpf on;`. This configuration is Linux-specific because it also uses `epoll` and systemd. |
| OpenSSL | **1.1.1** | Required for TLS 1.3 and nginx's HTTP/3 support. |
| systemd | **249** | Required for the service sandbox, including `ProtectProc` and `SocketBindDeny`. |
| Certbot | A currently supported release | Required only for the included ACME renewal service and timer. |

nginx 1.29.3 is the minimum version that can parse the complete configuration;
it is not a recommendation to deploy an obsolete release. Use a
currently supported stable or mainline nginx release that provides the build
features below.

NGINX Plus is not required. The configuration works with an appropriately
built nginx Open Source binary and does not rely on Plus-only directives.

## Required nginx build features

Check the installed binary with:

```sh
nginx -V 2>&1
```

The build must provide these non-default modules:

- `--with-http_ssl_module`
- `--with-http_v2_module`
- `--with-http_v3_module`
- `--with-http_gzip_static_module`
- `--with-http_stub_status_module`
- `--with-http_sub_module` when `includes/relativeurls.conf` is used

The normally built HTTP proxy, FastCGI, cache, map, geo, rewrite, access,
gzip, and limit-request modules must not have been disabled. Building nginx
requires the corresponding OpenSSL, zlib, and PCRE2 (or compatible PCRE)
development libraries.

The following dynamic Brotli modules must also be installed under nginx's
module directory:

```text
ngx_http_brotli_filter_module.so
ngx_http_brotli_static_module.so
```

They must be built for the exact nginx binary and compatible configure
arguments in use. A module copied from a different nginx build may fail to
load even when the version number appears similar.

## Host prerequisites

The checked-in paths assume this layout:

| Path or resource | Requirement |
| --- | --- |
| `/etc/nginx` | Deployment root for this repository's nginx files. |
| `/etc/nginx/mime.types` | nginx MIME type database, normally supplied by the nginx package. |
| `/etc/nginx/fastcgi_params` | FastCGI parameter file, required by the PHP-FPM includes. |
| `/usr/sbin/nginx` | nginx binary used by `systemd/nginx.service`. Adjust the unit if the package installs it elsewhere. |
| `/bin/certbot` | Certbot binary used by `systemd/certbot.service`. Adjust the unit if needed. |
| `/bin/systemctl` | systemctl binary used by the Certbot post-renewal reload. |
| `nginx` user and group | Worker identity configured by `nginx/nginx.conf`. |
| `/run/nginx` | PID directory; created by the nginx unit. |
| `/run/lock/nginx` | Lock directory; created by the nginx unit. |
| `/var/log/nginx` | nginx log directory; created by the nginx unit. |
| `/var/lib/nginx/client_tmp` | Writable client-body temporary directory. |
| `/var/lib/nginx/fastcgi_tmp` | Writable FastCGI temporary directory. |
| `/var/lib/nginx/proxy_tmp` | Writable proxy temporary directory. |
| `/var/lib/nginx/fastcgi` | Writable FastCGI cache directory when PHP or WordPress caching is enabled. |
| `/var/www/letsencrypt` | Root-owned ACME HTTP-01 webroot; mode `0750` with group `nginx` is recommended so Certbot can write and nginx can read. |

The host firewall must allow TCP ports 80 and 443. Allow UDP port 443 as well
to make HTTP/3 available; clients fall back to HTTP/2 or HTTP/1.1 when UDP is
unavailable.

## Deployment layout

- `nginx/nginx.conf` is the top-level configuration.
- `nginx/includes/` contains reusable behavior included by site definitions.
- `nginx/stubs/` contains installer-selected `http {}`-level policies, maps,
  cache zones, and rate-limit zones. `nginx.conf` loads every `*.conf` stub
  present on the deployed host, so the installer must copy only the features
  that host needs.
- `nginx/sites/` contains public-safe site configuration.
- `nginx/upstreams/` is reserved for deployment-specific upstream definitions.
  Its contents are ignored by Git, while its `.gitignore` keeps the empty
  directory in the repository.
- `systemd/` contains the nginx service and Certbot renewal units.

Deploy the selected contents of `nginx/` to `/etc/nginx/`, preserving this
directory structure. The installer must populate `stubs/` with the features
selected for that host; copying every source stub enables every optional
feature. Install the unit files in the host's system unit directory only after
reviewing their absolute paths for that distribution.

The selected configuration has these stub dependencies:

| Stub | Install when |
| --- | --- |
| `tls.conf` | Any selected site listens with `ssl`, including `_https_.conf`. This is required for HTTPS deployments. |
| `upstreamfallback.conf` | The baseline `security-headers.conf` include is enabled. This is required by the provided `nginx.conf`. |
| `ratelimit.conf` | A selected site or include uses `limit_req` or `limit_conn`, including `_http_.conf`, `http.conf`, and `wordpress-by-tag.conf`. |
| `fastcgi-cache.conf` | WordPress FastCGI caching is enabled through `wordpress-cache.conf`. |
| `websocket.conf` | A reverse-proxy site uses `$connection_upgrade`. |
| `gzip.conf` | gzip response compression is desired. |
| `brotli.conf` | Brotli response compression is desired and the modules loaded by `nginx.conf` are installed. |

The TLS stub deliberately omits `ssl_stapling off` because stapling is already
disabled by default, and whether to enable it depends on the certificate
authority and deployment. Experimental curve configuration, including hybrid
post-quantum groups, should be added only after the deployed nginx and TLS
library versions have been tested. `ssl_protocols` is selected before nginx
can apply an SNI-selected virtual host's configuration, so a deployment that
overrides it must do so on the listener's default server rather than only on a
non-default site.

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
identity, raise file limits, signal workers, and initialize the eBPF map used
by `quic_bpf on;`. Those controls require host-specific validation against the
installed kernel and nginx build. `LimitMEMLOCK=64M` is provided for the eBPF
map.

## Site configuration contract

The shared configuration is a baseline, not an application policy engine.
Individual sites and upstream applications may set stricter or
application-specific behavior.

### Security headers

`includes/security-headers.conf` supplies fallback values for common security
headers. Upstream applications may provide their own values for CSP,
`X-Content-Type-Options`, `Referrer-Policy`, `X-Frame-Options`, and
`Permissions-Policy`; nginx uses the upstream value instead of duplicating it.
For proxied responses, nginx hides upstream HSTS and supplies the edge policy.
FastCGI applications should not emit HSTS, or the site must hide that header
with `fastcgi_hide_header`, to avoid duplicate values.

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
- provision the writable FastCGI cache directory when caching is enabled; and
- review the WordPress cache exclusions and response-header handling against
  the application's authentication, personalization, and cache policy.

The shared WordPress cache configuration intentionally ignores upstream
`Cache-Control` and `Expires` headers. Do not enable it for an application
unless that edge-controlled cache behavior is understood and desired.

## Certbot renewal behavior

`systemd/certbot.timer` invokes `systemd/certbot.service`. The service always
attempts an nginx reload after Certbot exits, including when Certbot reports a
partial renewal failure. This is intentional: certificates that did renew
successfully must be loaded even when another certificate in the same run did
not renew. The reload runs from `ExecStopPost`, so Certbot's original nonzero
status is preserved rather than ignored. A failed nginx reload also causes the
unit to fail visibly.

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

## Validation

Run these checks on the target host before enabling the service:

```sh
nginx -v
nginx -V 2>&1
sudo nginx -t -c /etc/nginx/nginx.conf
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

The root and `nginx/upstreams/` `.gitignore` files enforce the common cases,
but they are not a substitute for reviewing the staged diff.

## References

- [nginx HTTP/2 module](https://nginx.org/en/docs/http/ngx_http_v2_module.html)
- [nginx HTTP/3 module](https://nginx.org/en/docs/http/ngx_http_v3_module.html)
- [nginx build options](https://nginx.org/en/docs/configure.html)
- [nginx SSL module](https://nginx.org/en/docs/http/ngx_http_ssl_module.html)
- [ngx_brotli build instructions](https://github.com/google/ngx_brotli)
- [Certbot renewal documentation](https://eff-certbot.readthedocs.io/en/stable/using.html#renewing-certificates)
