# OpenSSH baseline

Drop-in hardening for the distribution sshd: a non-default port, no direct
root login, and key-only authentication. Nothing here replaces
`/etc/ssh/sshd_config`; the files layer onto it through the distribution's
`sshd_config.d` include.

## How the drop-ins compose

sshd uses the **first obtained value** for every keyword — the opposite of
nginx's closest-scope-wins model. The stock RHEL-family `sshd_config` begins
with `Include /etc/ssh/sshd_config.d/*.conf`, so drop-ins beat the main file,
and within the directory the lexically **earliest** file wins each keyword.
The numbering scheme follows from that:

| Range | Owner |
| --- | --- |
| `10–39` | Deployment-local overrides; an earlier number beats the baseline. |
| `40` | This baseline. |
| `50` | Distribution and cloud-init files (`50-redhat.conf`, `50-cloud-init.conf`); the baseline deliberately sorts before them so a cloud image cannot re-enable password authentication. |

`KbdInteractiveAuthentication no` accompanies `PasswordAuthentication no`
because PAM's interactive path can otherwise still prompt for a password;
both are required for the policy to mean what it says. This mechanism needs
the distribution `Include` line, present on the supported RHEL 9 and newer
platforms; verify it exists before relying on the drop-ins.

`Port` is the exception to first-value-wins: multiple `Port` directives
accumulate and sshd listens on all of them. The stock configuration keeps
`#Port 22` commented, so the drop-in yields 2356 alone — but an uncommented
`Port` anywhere else silently keeps that listener open too. The validation
below therefore asserts exactly one effective port.

## Installation — lockout-safe order

Keep the working session open until the final step, and confirm out-of-band
console access exists before starting. The standard two-phase workflow makes
the transition explicit. `--authorized-key-ready` records that a non-root
administrator already has a working authorized key; it is a required guard,
not an automated key installer:

```sh
ssh/install --plan
sudo ssh/install

ssh/setup --plan --phase prepare --authorized-key-ready
sudo ssh/setup --phase prepare --authorized-key-ready
```

Prepare installs the key-only baseline, labels and opens TCP 2356, and adds a
temporary early `Port 22` drop-in. It validates that sshd will listen on
exactly 22 and 2356 before reloading. From a **new terminal**, prove the new
path:

```sh
ssh -p 2356 HOST
ssh -p 2356 -o PreferredAuthentications=password,keyboard-interactive -o PubkeyAuthentication=no HOST   # must be refused
ssh -p 2356 root@HOST                                                                                   # must be refused
```

Run finalize from that new port-2356 session. It refuses any other SSH
session unless `--console-confirmed` explicitly records out-of-band console
access:

```sh
ssh/setup --plan --phase finalize
sudo ssh/setup --phase finalize
```

Finalize removes the transition file, requires exactly port 2356, reloads
sshd, and only then removes the built-in `ssh` service and raw TCP 22
allowance from the selected firewalld zone. Existing sessions remain open.

The equivalent manual prepare sequence is:

```sh
install -m 0644 ssh/sshd_config.d/*.conf /etc/ssh/sshd_config.d/
semanage port -a -t ssh_port_t -p tcp 2356
install -m 0644 firewalld/services/ssh-hardened.xml /etc/firewalld/services/
firewall-cmd --reload
firewall-cmd --permanent --add-service=ssh-hardened
firewall-cmd --reload
sshd -t
systemctl reload sshd
```

The `ssh-hardened` firewalld service is this port's definition of record;
`firewalld/README.md` covers the service files. On a host without them,
`firewall-cmd --permanent --add-port=2356/tcp` is the raw equivalent.

SELinux confines sshd to `ssh_port_t`; without the `semanage` rule the
daemon cannot bind 2356 under enforcing mode. If the rule already exists,
`semanage port -a` fails — use `-m` instead. Reload does not drop
established sessions.

Only after the new session works, close port 22 in a manual cutover (the
built-in `ssh` service is distinct from `ssh-hardened`):

```sh
firewall-cmd --permanent --remove-service=ssh
firewall-cmd --reload
```

## Validation

```sh
sshd -t
sshd -T | grep -E '^(port|permitrootlogin|passwordauthentication|kbdinteractiveauthentication) '
test "$(sshd -T | grep -c '^port ')" = 1
```

`sshd -T` prints the effective merged configuration; expect `port 2356`,
`permitrootlogin no`, `passwordauthentication no`, and
`kbdinteractiveauthentication no`. CI installs these drop-ins onto the stock
`sshd_config` of Rocky Linux 9 and CentOS Stream 10 and asserts exactly
those effective values.

`fail2ban/setup --ssh-phase prepare` watches both 22 and 2356 during the
transition; rerun it with `--ssh-phase final` after finalization. The port is
obscurity, not security — the authentication policy is the control, and the
port only quiets scanner log noise.
