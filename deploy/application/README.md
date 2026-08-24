# Application deployment standard

This directory is the canonical source for a reusable application deployment
contract. It combines transaction security, immutable release activation, and
crash-safe recovery while keeping project-specific behavior behind narrow,
reviewed adapters.

The current normative contract is [standard-v1.md](standard-v1.md).

Project profiles are JSON documents validated by
[`validate_profile.py`](validate_profile.py). The schema deliberately rejects
unknown fields and arbitrary command fragments. The public example contains no
site, customer, hostname, key, or repository-specific data:

```sh
python3 deploy/application/validate_profile.py \
    deploy/application/profiles/example_node_app.json
```

## Candidate gold-bundle renderer

`render_core.py` produces the complete generic host bundle: the forced-command
gateway, durable trigger state machine, immutable release finalizer, isolated
candidate preflight, activation and rollback recovery, dependency helpers,
hardened systemd and OpenSSH policy, derived SELinux policy, fail-closed setup,
and non-mutating live verifier. It records the standard revision, profile
digest, generated file modes, and SHA-256 manifest in a deterministic bundle:

```sh
python3 deploy/application/render_core.py \
    --profile deploy/application/profiles/example_node_app.json \
    --source-revision 0123456789abcdef0123456789abcdef01234567 \
    --output /tmp/example-deployment-core
```

The output deliberately records `conformanceStatus: core-only` until the
remaining real-host and private-adapter release gates in
[`ROADMAP.md`](ROADMAP.md) pass. That status is a release-maturity boundary, not
permission to install the bundle. Private projects MUST NOT vendor or install a
rendered bundle while this repository still emits `core-only`.

## Exact bundle conformance

`verify_bundle.py` independently rerenders a pinned profile and standard source
revision, then compares the vendored tree byte-for-byte. It rejects missing or
extra entries, content or mode drift, symbolic links, hard links, special files,
and an incorrect source revision:

```sh
python3 deploy/application/verify_bundle.py \
    --profile deploy/application/profiles/example_node_app.json \
    --source-revision 0123456789abcdef0123456789abcdef01234567 \
    --bundle /tmp/example-deployment-core
```

Conformance proves only that a bundle is the exact deterministic output for its
inputs. It does not override the manifest's `conformanceStatus` or the release
gates.

## Distribution model

After version 1 is released, projects vendor a rendered, immutable copy of the
runtime bundle into their repository. Hosts install that reviewed copy into a
root-owned secure tree. Live deployments never execute code from this
repository over the network and never depend on a mutable shared checkout.

The standard owns:

- restricted, transaction-bound SSH and rsync ingress;
- durable trigger claims and append-only terminal results;
- manifest snapshot and dependency trust boundaries;
- immutable release promotion, isolated preflight, activation, rollback, and
  crash recovery;
- root-owned systemd, OpenSSH, SELinux, and verification contracts; and
- audit retention and operator recovery semantics.

Each private project profile supplies only application identity and approved
variation points such as the health probe, build validator, static-content
paths, dependency adapter, service limits, and retention policy. Private nginx
site configuration remains in the adopting project: domains, certificates,
trusted proxy ranges, and other site data are prohibited from this public
standard repository.

## Adoption rule

A project is conforming only when the rendered bundle and its tests satisfy the
entire normative contract. Copying selected scripts or describing a deployment
as "similar" is not conformance. Compatibility modes may be useful during a
migration, but they are not the gold deployment standard.
