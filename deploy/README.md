# Deployment tooling

`install-nginx` renders a complete nginx install tree from the repository
sources and the selected feature profiles. It only ever writes a new staging
directory — never the live `/etc/nginx` — so the rendered result can be
reviewed and installed as a deliberate overlay.

## Usage

```sh
deploy/install-nginx --check
deploy/install-nginx --list-profiles
deploy/install-nginx --output /tmp/nginx-install \
    --profile gzip \
    --profile websocket
```

The `baseline` profile is always selected. The output directory must not
exist; refusing to reuse one guarantees stale or deselected stubs cannot
survive from an earlier render. Each render writes an `INSTALL-PROFILE`
manifest recording the selected profiles and stubs, and generates a private
`quic_host.key` when the QUIC stub is selected.

Install the reviewed result over the package-provided `/etc/nginx`,
preserving package files (`mime.types`, `fastcgi_params`) and
deployment-local content (`sites`, `upstreams`, `trusted-proxies`), as the
[root README](../README.md) describes.

## Profiles

Profiles are line-per-stub manifests in `profiles/`. The profile table and
per-stub dependency rules live in the root README. Constraints enforced by
`--check` and CI:

- every stub under `nginx/stubs/` must be assigned to at least one profile;
- profile entries must be repository-relative stub paths, not symlinks; and
- profile names are lowercase alphanumerics and hyphens.

Adding a feature therefore means adding the stub, assigning it to a profile,
and updating the root README's tables in the same change.

## Validation

CI shellchecks the installer, runs `--check`, exercises a full render, and
verifies the refusal to overwrite an existing output. It then renders a CI
profile and runs `nginx -t` against the pinned stable and mainline nginx.org
packages on Rocky Linux 9. The Brotli modules cannot be loaded by stock CI
packages, so the brotli profile is validated for coverage in CI and must be
syntax-tested with the exact modules on the target host.
