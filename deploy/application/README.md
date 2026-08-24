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

## Distribution model

Projects vendor a rendered, immutable copy of the runtime bundle into their
repository. Hosts install that reviewed copy into a root-owned secure tree.
Live deployments never execute code from this repository over the network and
never depend on a mutable shared checkout.

The standard owns:

- restricted, transaction-bound SSH and rsync ingress;
- durable trigger claims and append-only terminal results;
- manifest snapshot and dependency trust boundaries;
- immutable release promotion, isolated preflight, activation, rollback, and
  crash recovery;
- root-owned systemd, OpenSSH, SELinux, and verification contracts; and
- audit retention and operator recovery semantics.

Each private project profile supplies only application identity and approved variation
points such as the health probe, build validator, static-content label rules,
dependency adapter, service limits, and retention policy.

## Adoption rule

A project is conforming only when the rendered bundle and its tests satisfy the
entire normative contract. Copying selected scripts or describing a deployment
as "similar" is not conformance. Compatibility modes may be useful during a
migration, but they are not the gold deployment standard.
