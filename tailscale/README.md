# Tailscale server baseline

Three-phase setup for tagged Linux servers using Tailscale SSH
for human administration while retaining the repository's native OpenSSH
listener on TCP 2356 for hardened application deployments.

Tailscale SSH and native OpenSSH are separate servers. Tailscale intercepts
TCP 22 only on the host's Tailscale addresses; it does not modify
`/etc/ssh/sshd_config` or `authorized_keys`. The native deployment listener on
2356 therefore remains available through its existing network path and
authentication controls.

## Security model

| Path | Authentication and authorization | Local account |
| --- | --- | --- |
| Human administration | Tailscale identity, tailnet SSH policy, and `check` | Explicit named administrator account |
| Application deployment | Native OpenSSH key or certificate restrictions | Dedicated deployment account |
| Recovery | Linode or Azure out-of-band console | Existing break-glass procedure |

Do not use `autogroup:nonroot` for tagged servers. It allows every non-root
local account named by the client, which would include deployment identities.
The tailnet policy must name the intended administrator account explicitly and
must deny both `root` and the deployment account.

Before enrollment, define the server tag, the narrow network grant, the SSH
rule, and positive and negative `sshTests` in the tailnet policy. A conceptual
policy shape is:

```json
{
  "tagOwners": {
    "tag:mikinho-server": ["group:infrastructure-admins"]
  },
  "grants": [
    {
      "src": ["group:infrastructure-admins"],
      "dst": ["tag:mikinho-server"],
      "ip": ["tcp:22"]
    }
  ],
  "ssh": [
    {
      "action": "check",
      "checkPeriod": "1h",
      "src": ["group:infrastructure-admins"],
      "dst": ["tag:mikinho-server"],
      "users": ["NAMED_ADMIN_ACCOUNT"]
    }
  ]
}
```

Replace the example tag, group, and local account with reviewed values. Add
`sshTests` proving that the administrator account is checked and that `root`,
the deployment account, other users, and unapproved sources are denied.

## Installation

`install` supports RHEL, Rocky Linux, and CentOS Stream versions 9 and 10. It
uses Tailscale's official stable RPM repository, verifies packages through the
repository's RPM and metadata signatures, installs `tailscale`, and enables
`tailscaled.service`. It does not authenticate the node or enable Tailscale
SSH:

```sh
tailscale/install --plan
sudo tailscale/install
```

The repository URL follows Tailscale's official installer mapping: versioned
RHEL and CentOS Stream repositories are used directly, while Rocky Linux uses
Tailscale's generic Fedora RPM repository.

## Setup

Setup is intentionally split so an enrolled server cannot begin accepting
Tailscale SSH under an unreviewed default policy. `--policy-ready` records that
the explicit policy and its tests have already been installed; it does not
inspect or modify the tailnet policy.

Join the server with at least one reviewed tag. DNS and subnet-route acceptance
remain disabled so enrollment cannot unexpectedly change server name
resolution or routing:

```sh
tailscale/setup --plan \
    --phase join \
    --policy-ready \
    --advertise-tag tag:mikinho-server \
    --hostname mikinho-server

sudo tailscale/setup \
    --phase join \
    --policy-ready \
    --advertise-tag tag:mikinho-server \
    --hostname mikinho-server
```

The interactive `tailscale up` command prints an authentication URL. Complete
that authentication, confirm the expected tag and device identity in the
Tailscale admin console, and verify the policy tests passed. Tailscale SSH
remains disabled after this phase.

Enable it separately:

```sh
tailscale/setup --plan --phase enable-ssh --policy-ready
sudo tailscale/setup --phase enable-ssh --policy-ready
```

The apply path refuses to continue unless `tailscaled.service` is active, the
node has a Tailscale IPv4 address, `sshd.service` is active, and native sshd's
effective configuration includes TCP 2356. When invoked over SSH, it also
requires the current session to use port 2356. It enables only the Tailscale
SSH preference and then revalidates the native deployment listener.

From a second terminal, prove Tailscale SSH into every named administrator
account. After confirming the Linode or Azure console path, remove those human
accounts from native OpenSSH without affecting the dedicated deployment
identities:

```sh
tailscale/setup --plan \
    --phase restrict-native \
    --policy-ready \
    --tailscale-ssh-confirmed \
    --console-confirmed \
    --deny-native-user michael

sudo tailscale/setup \
    --phase restrict-native \
    --policy-ready \
    --tailscale-ssh-confirmed \
    --console-confirmed \
    --deny-native-user michael
```

This phase installs
`/etc/ssh/sshd_config.d/20-tailscale-human-deny.conf`, validates the effective
`DenyUsers` policy, reloads native sshd transactionally, and restores the
previous file if validation or reload fails. Existing SSH sessions survive a
successful reload, so keep the port-2356 session open while proving both the
new Tailscale login and the deployment login. The restriction applies only to
native OpenSSH; Tailscale SSH uses its own server and tailnet authorization.

## Validation

Keep the existing port-2356 session open while testing from a second terminal:

```sh
tailscale status
tailscale get ssh
ssh NAMED_ADMIN_ACCOUNT@TAILSCALE_MAGICDNS_NAME
ssh -p 2356 DEPLOYMENT_ACCOUNT@PUBLIC_OR_PRIVATE_DEPLOYMENT_NAME
```

Also prove the negative cases: Tailscale SSH as `root` or the deployment
account must fail, an unapproved tailnet identity must fail, and native
OpenSSH must reject every account passed through `--deny-native-user`. A
Tailscale connection does not exercise the native `sshd_config` or
`authorized_keys` restrictions, so both planes require their own tests.
