# MongoDB security and transaction baseline

This component defines a reusable MongoDB Community 8.0 LTS baseline for EL9
x86_64 servers. Every deployment is an **initialized single-node replica set**,
including a database used only by applications on the same host. That makes
transactions available without claiming high availability that one member
cannot provide.

The repository contains no deployment names, addresses, database names,
usernames, passwords, certificates, backups, or customer data. Supply those
only at deployment time through reviewed host-local files and configuration.

## Supported models

| Model | Listener | Transport | Intended use |
| --- | --- | --- | --- |
| Local single-node replica set | `127.0.0.1:27017` only | Loopback | Database and its application services share one host. |
| Networked single-node replica set | Loopback plus one explicit RFC 1918 address | TLS required | A dedicated database host serves explicitly authorized application hosts over a private network. |

Both models require:

- MongoDB Community 8.0 LTS from MongoDB's official EL9 repository;
- one initialized, writable replica-set primary;
- SCRAM-SHA-256 authentication and authorization;
- `enableLocalhostAuthBypass: false` after initialization;
- a private replica-set key file, even though a single member has no peer
  traffic;
- server-side JavaScript disabled;
- SELinux enforcing with the distribution MongoDB policy and default RPM
  paths;
- separate administrative and application identities;
- one application identity per application database, with exactly `readWrite`
  on that database;
- encrypted storage, encrypted and tested backups, protected logs, patching,
  time synchronization, and capacity monitoring.

MongoDB's [security checklist](https://www.mongodb.com/docs/manual/administration/security-checklist/)
is the upstream authority. The [MongoDB 8.0 production notes](https://www.mongodb.com/docs/v8.0/administration/production-notes/)
cover storage and kernel guidance, and the [RHEL installation guide](https://www.mongodb.com/docs/v8.0/tutorial/install-mongodb-on-red-hat/)
defines the supported repository and package family.

## What a single node does and does not provide

Setting `replication.replSetName` alone is not enough. `mongodb/setup` runs
`rs.initiate()`, waits for a primary election, and the verifier requires both
the expected set name and `isWritablePrimary: true`. Applications may then use
multi-document transactions and retryable writes.

A single member still has an availability and durability boundary:

- there is no replica failover;
- host or storage loss makes the database unavailable;
- maintenance that restarts MongoDB interrupts database availability;
- rollback protection depends on tested, separate backups rather than another
  voting member.

Move to a reviewed three-member replica set when the recovery-time objective
requires automatic failover. At that point use CA-issued X.509 certificates
for member authentication rather than extending the single-node key-file
design. Do not add an arbiter merely to claim an odd vote count.

## Installation boundary

`mongodb/install`:

- accepts only RHEL, Rocky Linux, or CentOS Stream 9 on x86_64;
- installs the reviewed MongoDB 8.0.29 package set plus fixed `mongosh` and
  database-tools NEVRAs, with a recorded SHA-256 for every MongoDB RPM;
- pins MongoDB's complete server-key fingerprint
  `4B0752C1BCA238C0B4EE14DC41DE058A4E7DCA05`;
- verifies every downloaded RPM with `rpmkeys` and its `RSAHEADER` key ID;
- installs the MongoDB 8.0 repository disabled and refuses an enabled
  competing `mongodb-org` repository, preventing an unreviewed patch advance;
- refuses an already installed non-8.0 server;
- refuses package changes while `mongod.service` is active;
- installs a root-owned verifier; and
- leaves a fresh `mongod.service` stopped and disabled, while preserving an
  already configured service's enabled state during reviewed stopped-service
  patch maintenance.

This component intentionally selects the LTS release line, not a rapid release.
The exact reviewed baseline is 8.0.29; repository packages whose release notes
have not been published are not candidates merely because repository metadata
contains them. Updating the baseline requires release-note review plus updates
to every NEVRA and RPM SHA-256 in the installer and verifier.
Review the [MongoDB lifecycle schedule](https://www.mongodb.com/legal/support-policy/lifecycles)
before advancing the series. Minor and patch upgrades still require a current
backup, release-note review, an application maintenance decision, and the same
post-upgrade verification.

Privileged entry points refuse a user-writable checkout. Build only from a
clean Git revision, copy the resulting directory into a protected root-owned
location on the target, then verify and run that same staged directory:

```sh
mongodb/build-bundle --output /tmp/config-mongodb-REVISION
sudo install -d -o root -g root -m 0755 /root/config-mongodb-REVISION
sudo cp -a /tmp/config-mongodb-REVISION/. /root/config-mongodb-REVISION/
sudo chown -R root:root /root/config-mongodb-REVISION
sudo chmod -R go-w /root/config-mongodb-REVISION
sudo /root/config-mongodb-REVISION/mongodb/install --check-bundle
sudo /root/config-mongodb-REVISION/mongodb/install --plan
sudo /root/config-mongodb-REVISION/mongodb/install
```

Replace `REVISION` with the full source revision printed by the builder. Retain
`BUNDLE-METADATA` and `SHA256SUMS` with change evidence. `install` and `setup`
verify ownership, parent permissions, entry types, file link counts, the exact
Git revision field, and all recorded payload digests before making host changes.

## Credential boundary

Never pass a password in a command argument, paste it into a plan, or commit it.
Create each input as a one-link `root:root` mode `0600` file outside the
checkout. Each file contains one password line. Setup reads it only in the
root process and supplies it to `mongosh` through that process's environment;
the value is neither printed nor placed in the process argument list.

The administrative identity receives exactly `clusterAdmin` and
`userAdminAnyDatabase`. It is not an application identity and does not receive
`readWriteAnyDatabase` or `root`. Application identities receive exactly
`readWrite` on their own database. Create separate, narrowly scoped identities
for backup, monitoring, or restore jobs when those workflows are implemented.

Store each application's percent-encoded MongoDB URI in its protected runtime
secret file. Do not embed administrative credentials. Typical shapes are:

```text
mongodb://APP_USER:PERCENT_ENCODED_PASSWORD@127.0.0.1:27017/APP_DATABASE?authSource=APP_DATABASE&replicaSet=REPLICA_SET
mongodb://APP_USER:PERCENT_ENCODED_PASSWORD@mongodb.internal.example:27017/APP_DATABASE?authSource=APP_DATABASE&replicaSet=REPLICA_SET&tls=true&tlsCAFile=%2Fetc%2Fpki%2Fca-trust%2Fsource%2Fanchors%2Fmongodb-ca.pem
```

The URI is still a secret even though the password is encoded. Confirm that
logs, health endpoints, error pages, process listings, and deployment evidence
never reproduce it.

## Fresh local model

Initialization is destructive if aimed at the wrong data directory, so the
script accepts only the package's real, empty `/var/lib/mongo`, requires the
service inactive and disabled, and refuses an existing key file. It never
automates an existing-data migration.

```sh
mongodb/setup --plan \
    --phase initialize \
    --replica-set REPLICA_SET \
    --admin-user ADMIN_USER \
    --admin-password-file /root/mongodb-admin.password \
    --application-user APP_USER \
    --application-database APP_DATABASE \
    --application-password-file /root/mongodb-app.password

sudo mongodb/setup \
    --phase initialize \
    --replica-set REPLICA_SET \
    --admin-user ADMIN_USER \
    --admin-password-file /root/mongodb-admin.password \
    --application-user APP_USER \
    --application-database APP_DATABASE \
    --application-password-file /root/mongodb-app.password
```

The bootstrap listener is loopback-only. Setup initializes the set, creates
the users, installs authorization and the key file, disables the localhost
bypass, restarts into the final local configuration, and verifies the result.
If initialization fails, it stops and disables the service and preserves the
data for reviewed recovery; it does not delete or silently retry a partially
initialized database.

Add another application identity without changing listener policy:

```sh
sudo mongodb/setup \
    --phase add-application-user \
    --model local \
    --replica-set REPLICA_SET \
    --admin-user ADMIN_USER \
    --admin-password-file /root/mongodb-admin.password \
    --application-user SECOND_APP_USER \
    --application-database SECOND_APP_DATABASE \
    --application-password-file /root/mongodb-second-app.password
```

The operation refuses to replace or rotate an existing identity. Rotation is
a separate reviewed change so the old and new application generations can be
coordinated without an outage.

## Networked model

Start with a verified local model, then provision these deployment-specific
controls before exposing a listener:

1. A stable internal DNS name resolving locally to one explicit RFC 1918
   address.
2. A CA-issued server certificate whose SAN covers that name, stored with its
   matching, unencrypted private key in one `mongod:mongod` mode `0400` PEM
   file. Filesystem protection replaces an interactive passphrase because
   systemd must start unattended.
3. The public CA chain in one `root:root` mode `0644` file.
4. Persistent firewall or upstream controls allowing TCP 27017 only from the
   exact application-host sources. Do not enable a broad `mongodb` service,
   public zone, `0.0.0.0/0`, or entire private supernet for convenience.
5. A tested recovery path that does not depend on MongoDB being reachable.

The setup script validates certificate chain, SAN, expiry, DNS resolution,
permissions, and the secured local state. It then requires TLS on the private
listener and changes the sole replica-set member's advertised address to the
stable DNS name. Clients may connect without presenting their own X.509
certificate because the applications use SCRAM-SHA-256; authorization remains
mandatory, while invalid server certificates and hostnames remain rejected.

```sh
mongodb/setup --plan \
    --phase expose-network \
    --replica-set REPLICA_SET \
    --admin-user ADMIN_USER \
    --admin-password-file /root/mongodb-admin.password \
    --application-user APP_USER \
    --application-database APP_DATABASE \
    --application-password-file /root/mongodb-app.password \
    --bind-address PRIVATE_IPV4 \
    --member-host mongodb.internal.example \
    --tls-certificate-key-file /etc/pki/mongodb/server.pem \
    --tls-ca-file /etc/pki/mongodb/ca.pem \
    --network-controls-confirmed
```

`--network-controls-confirmed` is an operator assertion, not evidence. The
generic script deliberately does not edit firewalld: it cannot safely infer
the active interface, zone, existing rich rules, cloud controls, or every real
application source. Acceptance requires a successful TLS/authenticated test
from each allowed application host and a failed TCP test from at least one
representative denied host. Retain dated commands, source identities, and
results outside this repository.

## Existing databases

Do not point `--phase initialize` at an existing database. A reviewed migration
must account for the current package series, feature compatibility version,
data, replica state, users, application downtime, rollback, and backups.

The minimum migration sequence is:

1. inventory without extracting documents or credentials;
2. prove an encrypted backup and isolated restore;
3. stop application writes and take a final consistent backup;
4. restrict the listener to loopback before any unauthenticated bootstrap;
5. enable the single-node replica-set setting and initialize it;
6. create the administrative and per-application identities through the
   localhost exception;
7. install the final authorization/key-file configuration and verify the local
   model;
8. for a dedicated host, install exact-source network controls and TLS, then
   use the reviewed network transition;
9. update application secrets, start one application generation, and run
   transaction plus business-flow drills;
10. verify denied paths, backups, monitoring, and rollback evidence before
    closing the change.

Do not perform an in-place downgrade from a rapid release to 8.0. Build a new
8.0 target and use a version-compatible, tested export/restore or another
MongoDB-supported migration path.

## Verification and operations

Run the installed verifier on the database host after setup, certificate
renewal, package maintenance, firewall work, and any user or configuration
change. It checks the initialized primary, exact roles and SHA-256-only
mechanisms, authenticates as the application identity against its own database,
proves an allowed application-database read and a denied administrative read,
checks unauthenticated denial, listener scope, unique DNS resolution, TLS, file
identities, service state, the exact reviewed MongoDB package set, and the
`mongod_t` SELinux domain.

The verifier reports but does not automatically change host-wide tuning. XFS
is preferred for WiredTiger; ext4 is supported. MongoDB 8.0 expects transparent
huge pages enabled, `vm.zone_reclaim_mode=0`, and a low swappiness value. Apply
host tuning only after capacity and co-located workload review.

Operational acceptance also requires controls the Community server cannot
prove by configuration alone:

- encrypted volumes or equivalent provider disk encryption;
- encrypted, access-controlled backups with recorded restore tests;
- adequate filesystem and backup capacity alerts;
- time synchronization and host security updates;
- centralized service and authentication-failure monitoring without query or
  customer-data leakage;
- recovery objectives that acknowledge the single-node outage boundary; and
- application tests that start a session, commit a transaction, and confirm
  the intended business result.

Keep full-time diagnostic data capture available unless a documented privacy
or capacity decision says otherwise; it is local operational evidence, not a
substitute for centralized monitoring. Commercial audit logging and native
at-rest encryption features are not assumed by this Community baseline.

The installed file-log policy uses MongoDB's `reopen` behavior, rotates daily
or at 100 MiB, retains 14 compressed generations, and signals only the main
`mongod.service` process with `SIGUSR1`. Ship required security evidence to the
approved central destination before local retention expires. Change retention
only as a reviewed compliance and capacity decision; never use `copytruncate`.
