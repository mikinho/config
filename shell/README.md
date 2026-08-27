# Interactive shell baseline

This component applies conservative interactive Bash defaults for managed
servers. It prevents persistent command history and enforces an administrative
umask of at least `027`, and it identifies the managed environment in every
interactive Bash prompt. A shell still retains its current session history,
including normal up-arrow recall, but Bash writes history to `/dev/null`
instead of a file in the administrator's home directory.

The policy is a residual-data minimization control. It reduces the commands
available to a later administrator or IT provider from home-directory history;
it is not a security boundary between users with root access. A concurrent root
user can inspect or instrument processes, change shell startup files, or enable
other recording. Commands and arguments can also remain in `sudo`, Linux Audit,
systemd journal, process, application, terminal-recording, or backup data.

Never pass passwords, tokens, private keys, or other secrets as command-line
arguments. Use a protected file, a purpose-built secret interface, or an
interactive prompt appropriate to the application. Formal operational history
belongs in reviewed documentation and version-controlled automation rather
than personal shell history.

## Policy behavior

`profile.d/70-managed-environment-prompt.sh` reads the non-executable
`/etc/managed-environment` classification only when it is a root-owned,
mode-`0644`, non-symlink file containing exactly one of `PROD`, `TEST`, or
`DEV`. The prompt includes the classification, user, short hostname, and
working directory. Production root shells use a white-on-red `[PROD ROOT]`
warning. A missing, modified, weakly permissioned, or invalid classification
produces an `[UNTRUSTED]` prompt instead of guessing from the hostname or an
environment variable.

The classification is an operator assertion protected from unprivileged
changes; it is not proof of what workloads the host actually serves. Select it
from the deployment record during commissioning and review environment changes
as configuration changes.

`profile.d/80-admin-umask.sh` combines the current interactive Bash umask with
`027`. A normal `0022` or `0002` becomes `0027`; an already stricter value such
as `0037` or `0077` is preserved. Non-interactive shells are unchanged, and
systemd services must continue to declare their own file-creation policy and
explicit installation modes.

`profile.d/90-no-persistent-history.sh` applies only to interactive Bash
shells. It sets exported `HISTFILE=/dev/null` and marks the variable read-only
for that shell. It deliberately leaves `HISTSIZE` and `HISTFILESIZE` unchanged,
so in-session command recall remains available while no new history is written
to persistent storage.

The read-only variable prevents accidental reversal in later login startup
files; exported `HISTFILE` carries `/dev/null` into interactive child shells.
It does not prevent a root user from starting Bash without system profiles or
otherwise bypassing the policy. Bash does not itself read `/etc/profile.d` for
an independently launched non-login shell; distribution-default user
`.bashrc` files normally delegate to `/etc/bashrc`, but the enforceable baseline
is the administrator's login shell and its descendants.

The setup does not delete existing `.bash_history` files. Their removal is a
separate, destructive retention decision that must account for backups, audit
requirements, and any active incident investigation.

## Installation

Review a non-mutating plan, then apply the policy:

```sh
shell/setup --plan --environment PROD
sudo shell/setup --environment PROD
```

The setup is rerunnable, refuses symbolic-link sources and targets, installs
root-owned mode-`0644` drop-ins and classification, and verifies a fresh Bash
login. Verification requires the selected environment in `PS1`, a umask
containing every bit in `027`, `HISTFILE=/dev/null`, a positive `HISTSIZE`, and
a read-only `HISTFILE`.
Remove or park older profile fragments that set `HISTSIZE=0` before applying
this baseline, or name one exact legacy fragment in a rollback-protected
migration:

```sh
shell/setup --plan --environment PROD --replace /etc/profile.d/OLD.sh
sudo shell/setup --environment PROD --replace /etc/profile.d/OLD.sh
```

The replacement path must be one root-owned, mode-`0644`, non-symlink `.sh`
file directly under `/etc/profile.d`; globs and directories are rejected. The
old and managed files are restored if installation or login validation fails.
Without removal or replacement, setup fails rather than silently accepting the
loss of same-session history.

Existing sessions retain the environment with which they started. Open a new
session after installation and verify:

```sh
bash -lic 'printf "PS1=%s HISTFILE=%s HISTSIZE=%s UMASK=%s\n" \
    "$PS1" "$HISTFILE" "$HISTSIZE" "$(umask)"'
```

Expect the selected environment label plus user, host, and path in `PS1`,
`HISTFILE=/dev/null`, a positive `HISTSIZE`, and a umask of `0027` or a stricter
value.

## Integrity audit

`shell/verify` is a read-only audit of the shell startup trust boundary. It
checks `/etc/profile`, `/etc/bashrc`, `/etc/managed-environment`,
`/etc/profile.d` and its entries, selected accounts' Bash and SSH startup files,
and every component of the supplied `PATH`. Files and directories must not be
symbolic links or group/world writable. Global startup files and PATH
directories must be owned by root; user startup files may be owned by that
account or root. PATH may not contain empty, current-directory, relative,
missing, or unsafe components, and every ancestor back to the filesystem root
is checked.

Run from a root login to audit root's effective administrative PATH. When
invoked through `sudo`, root and `SUDO_USER` are selected automatically:

```sh
sudo shell/verify
```

Select accounts explicitly when commissioning a host:

```sh
sudo shell/verify --user root --user deploy
```

`--path VALUE` audits an exact alternate PATH without changing how the verifier
finds its own tools. `--root DIRECTORY` audits an offline filesystem tree and
maps expected root ownership to the owner of that tree; its accounts are read
from `DIRECTORY/etc/passwd`.
