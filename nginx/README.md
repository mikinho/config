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
| `sites/` | Public-safe site definitions, including the default servers. |
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
  `stubs/http/upstreamfallback.conf`; an application-supplied header wins and
  the edge stays silent.
- `sites/_http_.conf` and `sites/_https_.conf` are the default servers for
  unknown host names (444, and 421 with `ssl_reject_handshake`). Site files
  must declare their own `server_name` and must not claim `default_server`.
- TLS policy is shared in `stubs/http/tls.conf`. `ssl_protocols` is selected
  before SNI routing, so protocol changes belong there or on the listener's
  default server, never only on a non-default site.
- PHP-FPM integration is by tag: define `$site_tag`, provide the socket at
  `/run/$site_tag/php-fpm.sock`, and include the `*-by-tag.conf` file. Use
  `php-fpm-path-info-by-tag.conf` only for a narrowly scoped location that
  genuinely requires PATH_INFO.

## Validation

Render a profile and syntax-check it exactly as CI does, or run
`sudo nginx -t -c /etc/nginx/nginx.conf` on the target host. CI exercises
`nginx -t` for every push against the pinned stable and mainline nginx.org
packages on Rocky Linux 9; a change that adds a directive must stay within
the root README's syntax floor or advance it deliberately.
