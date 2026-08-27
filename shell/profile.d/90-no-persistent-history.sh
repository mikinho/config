# shell/profile.d/90-no-persistent-history.sh

#
# Author: Michael Welter <me@mikinho.com> - https://github.com/mikinho
#

# shellcheck shell=sh
# Managed by the config repository shell baseline.
# Keep interactive history in memory, but never persist it on this host.
if [ -n "${BASH_VERSION:-}" ]; then
    case $- in
        *i*)
            if [ "${HISTFILE:-}" != /dev/null ]; then
                HISTFILE=/dev/null
            fi
            export HISTFILE
            readonly HISTFILE
            ;;
    esac
fi
