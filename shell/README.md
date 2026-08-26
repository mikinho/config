# Interactive shell baseline

This component prevents Bash from persisting interactive command history on a
managed server. A shell still retains its current session history, including
normal up-arrow recall, but Bash writes history to `/dev/null` instead of a
file in the administrator's home directory.

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
shell/setup --plan
sudo shell/setup
```

The setup is rerunnable, refuses symbolic-link sources and targets, installs a
root-owned mode-`0644` drop-in, and verifies a fresh Bash login. The verification
requires `HISTFILE=/dev/null`, a positive `HISTSIZE`, and a read-only `HISTFILE`.
Remove or park older profile fragments that set `HISTSIZE=0` before applying
this baseline, or name one exact legacy fragment in a rollback-protected
migration:

```sh
shell/setup --plan --replace /etc/profile.d/OLD.sh
sudo shell/setup --replace /etc/profile.d/OLD.sh
```

The replacement path must be one root-owned, mode-`0644`, non-symlink `.sh`
file directly under `/etc/profile.d`; globs and directories are rejected. The
old and managed files are restored if installation or login validation fails.
Without removal or replacement, setup fails rather than silently accepting the
loss of same-session history.

Existing sessions retain the environment with which they started. Open a new
session after installation and verify:

```sh
bash -lic 'printf "HISTFILE=%s HISTSIZE=%s\n" "$HISTFILE" "$HISTSIZE"'
```

Expect `HISTFILE=/dev/null` and a positive `HISTSIZE`.
