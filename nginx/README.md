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
  overwrite `X-Real-IP` and `X-Forwarded-For` with `$remote_addr`; never append
  an untrusted incoming forwarding chain, and strip the legacy `Proxy` request
  header before it reaches an application environment. The trusted-proxy
  profile may update `$remote_addr` only from explicitly trusted immediate
  peers.
- `includes/relativeurls.conf` is a legacy opt-in, not shared WordPress policy.
  Do not enable response-body URL rewriting on an SEO-indexed site instead of
  generating correct absolute canonical and alternate-language URLs.

CI also starts the rendered safe profile and verifies these response-level
contracts: HTTPS redirection, inherited baseline headers, application CSP
preservation, untrusted forwarding-header replacement, internal-only
maintenance responses, and failure-only static asset logging.

## Host runtime setup

After a reviewed render has been assembled at `/etc/nginx` and the exact live
tree passes `nginx -t`, apply its surrounding host settings with:

```sh
nginx/setup --plan
sudo nginx/setup
```

The setup entry point verifies the installed nginx binary and live
configuration before changing anything, then applies the repository SELinux
registrations, additive firewalld service, systemd unit, logrotate policy,
QUIC sysctl limits, and host verification tools. Add `--quic-bpf` only when
the rendered nginx profile selected that feature. It never copies, renders,
or deletes anything under `/etc/nginx`; the reviewed tree remains a separate
deployment transaction.

`sites/sample_wp.conf.example` matches the `sample_wp` PHP-FPM pool and systemd
instance. Replace its domains, certificate paths, and site tag, then install it
as `sites/SITE_TAG.conf`; only the installed `*.conf` copy becomes active.
`sites/sample_node.conf.example` provides the corresponding reference for
Node.js reverse-proxied applications with static asset cache fallbacks.

## Validation

Render a profile and syntax-check it exactly as CI does, or run
`sudo nginx -t -c /etc/nginx/nginx.conf` on the target host. CI exercises
`nginx -t` for every push against the pinned stable and mainline nginx.org
packages on Rocky Linux 9. It activates `sample_wp.conf.example` only in the
ephemeral CI tree and generates a one-day self-signed certificate, so the
complete public WordPress site is parsed without making it part of an installed
baseline. It starts the safe profile for response-level policy checks, then
replaces the site's routing include with the cached variant and parses that
configuration too. A change that adds a directive must stay within the root
README's syntax floor or advance it deliberately.
