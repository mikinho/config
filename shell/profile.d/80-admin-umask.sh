# shellcheck shell=bash
# Managed by the config repository shell baseline.
# Prevent interactive administrators from creating world-accessible files.
if [ -n "${BASH_VERSION:-}" ]; then
    case $- in
        *i*)
            managed_current_umask=$(umask)
            managed_current_umask=${managed_current_umask#0}
            printf -v managed_hardened_umask '%03o' \
                "$((8#$managed_current_umask | 8#027))"
            umask "$managed_hardened_umask"
            unset managed_current_umask managed_hardened_umask
            ;;
    esac
fi
