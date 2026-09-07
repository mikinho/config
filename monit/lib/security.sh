#!/usr/bin/env sh

#
# Author: Michael Welter <me@mikinho.com> - https://github.com/mikinho
#

# Shared fail-closed checks for the installed control tree. Callers provide
# fail(), and invoke filesystem identity checks only on the Linux target.
monit_require_no_acl() {
    monit_acl_path=$1
    monit_acl=$(getfacl -cp -- "$monit_acl_path" 2>/dev/null) \
        || fail "cannot inspect ACL: $monit_acl_path"
    if printf '%s\n' "$monit_acl" \
        | grep -Eq '^(default:|user:[^:]|group:[^:]|mask:)'
    then
        fail "extended or default ACL requires explicit remediation: $monit_acl_path"
    fi
}

monit_require_private_fragment() {
    monit_private_path=$1
    case "${monit_private_path##*/}" in
        '' | .* | *[!A-Za-z0-9_.-]* | *.conf.conf)
            fail "unsafe Monit fragment basename: $monit_private_path" ;;
        *.conf) ;;
        *) fail "Monit fragment basename must end in .conf: $monit_private_path" ;;
    esac
    [ -f "$monit_private_path" ] && [ ! -L "$monit_private_path" ] \
        || fail "Monit fragment must be a non-symbolic regular file: $monit_private_path"
    monit_private_identity=$(stat -c '%u:%g:%a:%h' "$monit_private_path" 2>/dev/null) \
        || fail "cannot inspect Monit fragment: $monit_private_path"
    [ "$monit_private_identity" = 0:0:600:1 ] \
        || fail "Monit fragment must be root:root mode 0600 with one link; adopt it with --fragment: $monit_private_path"
    monit_require_no_acl "$monit_private_path"
    monit_require_flat_fragment "$monit_private_path"
}

# Explicit adoption can tighten a trusted root-owned 0644/0640 source to 0600.
# It must not bless an untrusted writer or an inode reachable through aliases.
monit_require_adoption_source() {
    monit_adoption_path=$1
    [ -f "$monit_adoption_path" ] && [ ! -L "$monit_adoption_path" ] \
        || fail "adoption source must be a non-symbolic regular file: $monit_adoption_path"
    monit_adoption_identity=$(stat -c '%u:%g:%h' "$monit_adoption_path" 2>/dev/null) \
        || fail "cannot inspect adoption source: $monit_adoption_path"
    [ "$monit_adoption_identity" = 0:0:1 ] \
        || fail "adoption source must be root:root with one link; supply a reviewed independent root-owned copy: $monit_adoption_path"
    monit_adoption_mode=$(stat -c '%a' "$monit_adoption_path" 2>/dev/null) \
        || fail "cannot inspect adoption source mode: $monit_adoption_path"
    [ "$((0$monit_adoption_mode & 0022))" -eq 0 ] \
        || fail "adoption source must not be writable by group or other: $monit_adoption_path"
    monit_require_no_acl "$monit_adoption_path"
    monit_require_flat_fragment "$monit_adoption_path"
}

# Only the managed main file may include files. Reject nested include tokens
# outside quoted strings/comments, including mixed-case and inline directives.
# Flattening keeps the complete effective tree enumerable without implementing
# a second Monit glob/parser or following deployment-controlled external paths.
# The file reaches awk through a redirection, never as an operand: awk reads a
# relative operand shaped like NAME=value as a variable assignment and then
# waits on standard input, which would inspect nothing.
monit_require_flat_fragment() {
    monit_flat_path=$1
    [ -r "$monit_flat_path" ] || fail "cannot read Monit fragment: $monit_flat_path"
    if awk '
        function finish_token() {
            if (tolower(token) == "include") found = 1
            token = ""
        }
        {
            for (i = 1; i <= length($0); i += 1) {
                character = substr($0, i, 1)
                if (quote != "") {
                    if (escaped) escaped = 0
                    else if (character == "\\") escaped = 1
                    else if (character == quote) quote = ""
                } else if (character == "#") {
                    finish_token()
                    break
                } else if (character == "\"" || character == sprintf("%c", 39)) {
                    finish_token()
                    quote = character
                } else if (character ~ /[[:space:]!@:{};,()%]/) {
                    finish_token()
                } else {
                    token = token character
                }
            }
            finish_token()
            escaped = 0
        }
        END { exit found ? 1 : 0 }
    ' < "$monit_flat_path"
    then
        return 0
    fi
    fail "nested include directives are unsupported; supply each flattened *.conf explicitly: $monit_flat_path"
}

monit_read_process_umask() {
    monit_umask_pid=$1
    case "$monit_umask_pid" in
        '' | *[!0-9]* | 0 | 1) return 1 ;;
    esac
    awk '$1 == "Umask:" { print $2; found = 1; exit }
        END { if (!found) exit 1 }' "/proc/$monit_umask_pid/status"
}
