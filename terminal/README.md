# Generic terminal readiness

This component gives administrators a conservative terminal compatibility
floor on supported RHEL-family servers. It installs the vendor packages that
provide:

- `tic`, for compiling reviewed terminfo source;
- `infocmp`, for inspecting and exporting terminfo definitions; and
- the standard `xterm-256color` entry, a broadly supported 256-color fallback.

On RHEL, Rocky Linux, and CentOS Stream 9 or 10, those interfaces are supplied
by the vendor `ncurses` and `ncurses-base` packages. Package release numbers
remain vendor-managed; the installer intentionally uses stable package names
instead of pinning an upstream ncurses version.

## Scope

The server baseline does not install a personal terminal emulator, force a
client's `TERM` value, or publish emulator-specific terminfo globally. A
client-side configuration may still select a newer terminal identity and
install its reviewed definition into a user's private terminfo database. The
server-side requirement here is simply that the standard fallback works and
the tooling needed for that private installation is present.

## Install and verify

Review the supported-platform package plan, then apply it as root:

```sh
terminal/install --plan
sudo terminal/install
```

The installer runs `terminal/verify` after package installation. The verifier
is read-only with respect to host configuration: it exports the system
`xterm-256color` definition, compiles it into a mode-`0700` temporary terminfo
database, queries only that isolated database, and removes it on exit.
The installer also publishes the reviewed verifier as the root-owned
`/usr/local/bin/verify-terminal-readiness` host tool so the strict deployment
audit can repeat the same check later.

Run the functional check independently at any time:

```sh
terminal/verify
/usr/local/bin/verify-terminal-readiness
```

A passing result proves that `tic`, `infocmp`, and the standard entry work
together. It does not prove that an arbitrary client-specific `$TERM` entry is
installed; check that exact name with `infocmp "$TERM"` after connecting.
