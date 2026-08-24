# Gold application deployment standard, version 1

The key words **MUST**, **MUST NOT**, **REQUIRED**, **SHOULD**, **SHOULD NOT**,
and **MAY** in this document are normative.

## 1. Goals and trust model

The standard deploys an application from an untrusted CI transfer session to a
reviewed, immutable release without granting the transfer identity a shell or
permission to mutate trusted control state.

The design assumes that a deploy key, a CI runner, or transferred application
content may be compromised. It does not attempt to survive compromise of root,
the application service account, the package repository used by the host, or
the reviewed root-owned deployment scripts.

The following principals MUST remain distinct:

| Principal | Permitted authority |
| --- | --- |
| deploy account | Start one serialized, token-bound write-only transfer into a unique candidate. |
| application account | Read and execute one committed release and write only its explicit runtime state. |
| nginx | Traverse the committed release, read explicitly labeled static content, and connect to the application socket. |
| systemd/root finalizer | Validate, prepare, preflight, promote, activate, recover, and publish audit results. |
| operator | Install reviewed host policy and perform explicit recovery actions. |

The deploy and application accounts MUST NOT be interchangeable and MUST NOT
belong to each other's primary groups merely to simplify permissions.

## 2. Required transaction lifecycle

Every deployment MUST use this state sequence:

1. The client creates a deployment token containing the immutable source
   revision, a bounded timestamp, and fresh cryptographic randomness.
2. A forced-command SSH gateway validates the exact command and token, takes an
   exclusive ingress lock, and creates a durable transfer marker in root-owned
   state before invoking restricted rsync.
3. Restricted rsync writes only to a new candidate directory. The receiver MUST
   enforce path confinement, refuse devices and special files, and durably
   synchronize transferred content before the session can publish completion.
4. The gateway atomically publishes one token-bound sentinel into a watched
   directory that the deploy account cannot mutate directly.
5. The root finalizer atomically claims that sentinel into private state and
   binds the claim to the current systemd invocation.
6. The finalizer validates candidate identity, entry types, metadata, ownership,
   permissions, filesystem boundaries, and application-specific build output.
7. Dependency manifests are snapshotted without following links and are checked
   for inode stability before dependency preparation begins.
8. Dependencies are installed or reused under the application account according
   to an exact dependency and runtime provenance identity.
9. A candidate service generation starts through an isolated socket and release
   pointer. Its health response MUST identify the exact deployment token.
10. Only a healthy candidate is atomically promoted into the immutable release
    namespace.
11. The finalizer durably records activation intent, changes the live release
    pointer atomically, restarts or reloads the live service, and confirms the
    exact deployment token through the live socket.
12. The finalizer publishes committed state, clears activation intent, and then
    publishes an append-only success result for the waiting gateway.

A failure at any step MUST publish or preserve enough root-owned state to prove
which token failed and to recover deterministically. The client MUST receive
success only after step 12.

## 3. Ingress and transfer boundary

- The deploy account MUST have no interactive shell, password, agent
  forwarding, X11 forwarding, port forwarding, PTY, or user-controlled
  environment.
- OpenSSH MUST apply one reviewed `ForceCommand` for the deploy account. The
  gateway MUST reject every command except the standard session command with
  one valid deployment token.
- Setup MUST install a temporary `DenyUsers` maintenance guard before changing
  deploy-account state. The guard MUST remain installed on incomplete setup.
- The gateway MUST serialize the complete transfer/finalization transaction,
  not only the rsync subprocess.
- The rsync receiver MUST be a reviewed version that supports the confinement
  and special-file refusal contract. A distribution package or organizationally
  signed package is preferred; a temporary bridge MUST retain complete build
  provenance.
- The deploy account MUST NOT create the watched sentinel, terminal result,
  activation intent, committed state, or audit claim directly.
- Legacy direct-rsync, trusted-client, and client-written-ready modes are
  prohibited.

## 4. Root-owned trigger and audit boundary

The trigger root MUST contain separate inbox, private state, and result
directories plus distinct ingress, publish, and session locks. Setup and
verification MUST enforce exact owner, group, mode, file type, and link-count
contracts for every control object.

Trigger transitions MUST be atomic on one filesystem. Invalid or ambiguous
input MUST be quarantined rather than discarded. A failed finalizer MUST retain
its private claim for audit and publish an append-only failure result when that
transition is safe. Success and failure result names and contents MUST bind the
source revision, deployment token, and finalizer invocation.

Recovery MUST support, at minimum:

- completing an interrupted terminal-result publication;
- resolving a stale transfer marker;
- inspecting, requeueing, or explicitly discarding a retained claim;
- recovering an interrupted activation from durable intent; and
- refusing automated recovery when live pointers diverge from recorded state.

Recovery commands MUST be explicit and auditable. Setup MUST NOT silently erase
unresolved work.

## 5. Candidate and release boundary

- Candidates MUST be unique real directories beneath one incoming root and MUST
  begin with no inherited executable dependencies.
- Incoming and release roots MUST share a filesystem so promotion is atomic.
- A candidate MUST be preflighted from an isolated, root-controlled pointer and
  socket. It MUST NOT receive live traffic before it proves token-specific
  readiness.
- Promotion MUST rename the validated candidate into a unique release
  directory. A release identifier MUST be immutable and MUST match reviewed
  deployment metadata inside the release.
- Release roots and their executable content MUST become non-writable to both
  deploy and application accounts before activation.
- The live service MUST execute through a relative, atomically replaced
  `current` link into the release namespace. A `previous` link MAY be retained
  for operator convenience, but durable activation state is authoritative.
- Health probes MUST traverse the candidate or live Unix socket directly,
  bypass proxies, and prove both application readiness and the exact deployment
  token.
- Activation failure MUST restore the previously committed pointer and verify
  the previous token. If both candidate and rollback fail, the deployment MUST
  remain failed with recovery evidence intact.

In-place application mutation is a migration mode, not a conforming gold
deployment.

## 6. Dependency boundary

- The finalizer MUST read dependency inputs only through bounded, no-follow,
  single-link regular-file snapshots created beneath a root-controlled work
  root.
- Snapshot creation MUST verify stable device, inode, size, and modification
  identity across the copy operation.
- The dependency hash MUST cover all install-affecting manifest and lockfile
  fields using deterministic canonicalization and MUST reject unsupported
  lockfile structure.
- Dependency reuse requires both an exact dependency hash and an exact runtime
  provenance identity. For Node.js this includes Node version and ABI, npm
  version, platform, architecture, and libc identity.
- The package manager MUST run as the application account with a private home,
  cache, and workspace. Root MUST NOT execute package lifecycle scripts.
- The manifests MUST be rehashed after installation and before dependencies are
  accepted into the candidate.
- Generated dependency provenance MUST be stored inside the immutable release
  and checked by the host verifier.

Projects MAY replace npm with another dependency adapter only when it provides
equivalent snapshot, non-root execution, deterministic identity, and provenance
guarantees.

## 7. Host policy boundary

The installed deployment surface MUST be rooted in directories that are owned
by root and not writable by deploy or application accounts. Generic setup MUST
install scripts, SSH policy, systemd units, and SELinux policy by atomic
replacement from a reviewed archive.

Private nginx site configuration is an adopter-owned adapter. It MUST remain in
the private application repository and MUST NOT be rendered into this public
standard. The adapter owns domains, certificates, trusted proxy ranges, and
other site-specific data; it MUST target the profile's live Unix socket, use the
shared nginx transport policy selected for that host, validate the fully
assembled nginx candidate before installation, and pass `nginx -t` before
reload. Generic setup and verification MAY validate nginx account membership,
service state, SELinux process domain, and direct socket reachability without
learning private site data.

The standard requires:

- SELinux enforcing with the targeted policy;
- a project-specific trigger type that lets `init_t` read and watch trigger
  objects but not create, rename, unlink, or write them;
- an application runtime domain compatible with nginx's reviewed Unix-socket
  connection rule;
- explicit static-content labels rather than broad labeling of source or
  dependency trees;
- systemd hardening, resource bounds, a private runtime directory, and a
  generation-specific readiness check;
- an adopter-owned nginx candidate test before site installation or reload;
  and
- effective OpenSSH policy verification for representative local and remote
  connection contexts before the maintenance guard is removed.

Setup MUST quiesce ingress and finalization before reconciling accounts or
trusted paths. On failure it MUST stop the path trigger, lock deploy-writable
top-level directories, and retain the maintenance guard.

## 8. Verification and evidence

Each rendered bundle MUST include a non-mutating verifier that checks the live
host against this contract. Verification MUST fail closed when a required tool,
policy rule, label, unit, script, state object, or health probe is unavailable.

CI for the standard and every adopting project MUST cover:

- exact forced-command acceptance and rejection cases;
- concurrency, stale-marker, quarantine, claim, completion, and recovery state
  transitions;
- symlink, hardlink, ownership, mode, inode-swap, and filesystem-boundary
  attacks;
- dependency hash and runtime provenance decisions;
- candidate preflight, atomic promotion, activation success, rollback success,
  rollback failure, and crash recovery;
- systemd and OpenSSH rendered-policy assertions;
- SELinux module compilation and effective allow/deny checks; and
- generated-bundle reproducibility and conformance to its project profile.

The deployment workflow MUST retain the source revision, deployment token,
workflow identity, terminal result, and host journal invocation needed to
reconstruct a deployment outcome without relying on mutable application files.

## 9. Project profile and approved adapters

A project profile MUST declare only data and bounded adapter selections. At
minimum it identifies:

- application tag, service user, deploy user, and deploy group;
- application, secure, runtime, trigger, state, cache, incoming, and release
  paths;
- the fixed Node runtime entrypoint, socket and PID names, and loopback policy;
- session command, service units, Unix sockets, and health route;
- deployment metadata path and token field;
- static-content paths from which the renderer derives exact SELinux label
  patterns;
- dependency adapter and runtime provenance adapter;
- candidate build validator;
- service resource limits and startup timeout; and
- release and audit retention policy.

Profiles MUST NOT contain arbitrary shell fragments. An application-specific
adapter is permitted only for behavior that cannot be expressed as validated
data. Adapters are root-owned executables with a documented input/output
contract and focused hostile-input tests.

## 10. Versioning and rollout

The renderer MUST record the standard version, source revision, project profile
digest, and generated-file manifest in every bundle. Generated bundles are
committed to the application repository so their review and deployment remain
atomic with application changes.

Changing a MUST-level invariant requires a new standard version. Compatible
hardening may increment the bundle revision within the same version. Rollout is
always staged:

1. render and test the bundle in `config`;
2. vendor it into one application and pass that application's full deployment
   suite;
3. install and verify it on the test/dev server with deployment ingress paused;
4. execute and audit a real deployment and rollback exercise; and
5. promote the same reviewed, immutable artifacts to additional hosts.

Application repositories retain their private profiles and adapters, but the
generic contract and renderer remain the independent source of truth.
