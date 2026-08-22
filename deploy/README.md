# Deployment tooling

`install-nginx` renders a complete nginx install tree from the repository
sources and the selected feature profiles. It only ever writes a new staging
directory — never the live `/etc/nginx` — so the rendered result can be
reviewed and installed as a deliberate overlay.

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
host before installing a render. It fails when the local nginx predates
1.29.3 (`add_header_inherit`, the floor in the root README's compatibility
table), and each render records the same floor as an `nginx-version-floor`
line in its `INSTALL-PROFILE` manifest so other tooling can check it.

The `baseline` profile is always selected. The output directory must not
exist; refusing to reuse one guarantees stale or deselected stubs cannot
survive from an earlier render. Each render writes an `INSTALL-PROFILE`
manifest recording the selected profiles and stubs, and generates a private
`quic_host.key` when the QUIC stub is selected.

Install the reviewed result over the package-provided `/etc/nginx`,
preserving package files (`mime.types`, `fastcgi_params`) and
deployment-local content (`sites`, `upstreams`, `trusted-proxies`), as the
[root README](../README.md) describes.

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
    --profile wordpress
```

Selection rationale, so the host's choices stay written down:

- `gzip` + `brotli` — both compressors; the applications precompress their
  build outputs, so `gzip_static`/`brotli_static` serve siblings and the
  runtime compressors cover proxied HTML and legacy assets.
- `quic-bpf` — the host kernel supports the QUIC reuseport eBPF map.
- `wordpress` — PHP sites remain on this host; drop the profile once the
  last one is retired.
- `websocket` deliberately not selected — no deployed site uses
  `$connection_upgrade`; a site adding WebSockets must add the profile in
  the same change.
- `trusted-proxy` not selected — clients connect directly, so
  `$remote_addr` is already the client. Select it only if a CDN or other
  proxy is ever placed in front, together with its `trusted-proxies/`
  entries.
- `post-quantum` not selected until the host's TLS provider passes the
  syntax test described under Validation.

## Validation

CI shellchecks the installer, runs `--check`, exercises a full render, and
verifies the refusal to overwrite an existing output. It then renders a CI
profile and runs `nginx -t` against the pinned stable and mainline nginx.org
packages on Rocky Linux 9. The Brotli modules and OpenSSL 3.5 hybrid group
cannot be loaded by stock CI packages, so the `brotli` and `post-quantum`
profiles are validated for coverage in CI and must be syntax-tested with the
exact modules and TLS provider on the target host.
