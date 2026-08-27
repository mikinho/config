# shell/profile.d/70-managed-environment-prompt.sh

#
# Author: Michael Welter <me@mikinho.com> - https://github.com/mikinho
#

# shellcheck shell=bash
# Managed by the config repository shell baseline.
# Display a trusted host classification without executing the classification file.

managed_configure_environment_prompt() {
    local managed_environment_file=$1
    local managed_expected_uid=$2
    local managed_environment=UNTRUSTED
    local managed_identity=
    local managed_line_count=
    local managed_prompt_tag=
    local managed_prompt_character='$'

    if [[ -f $managed_environment_file && ! -L $managed_environment_file ]]; then
        managed_identity=$(stat -Lc '%u:%a' "$managed_environment_file" 2>/dev/null \
            || stat -Lf '%u:%Lp' "$managed_environment_file" 2>/dev/null \
            || true)
        managed_line_count=$(wc -l < "$managed_environment_file" 2>/dev/null \
            || true)
        managed_line_count=${managed_line_count//[[:space:]]/}
        if [[ $managed_identity == "$managed_expected_uid:644" \
            && $managed_line_count == 1 ]]; then
            managed_environment=$(< "$managed_environment_file")
            case $managed_environment in
                PROD | TEST | DEV) ;;
                *) managed_environment=UNTRUSTED ;;
            esac
        fi
    fi

    if (( EUID == 0 )); then
        managed_prompt_character='#'
    fi

    if [[ -t 1 && ${TERM:-dumb} != dumb ]]; then
        case "$managed_environment:$EUID" in
            PROD:0) managed_prompt_tag='\[\033[1;37;41m\][PROD ROOT]\[\033[0m\]' ;;
            PROD:*) managed_prompt_tag='\[\033[1;37;41m\][PROD]\[\033[0m\]' ;;
            TEST:0) managed_prompt_tag='\[\033[1;30;43m\][TEST ROOT]\[\033[0m\]' ;;
            TEST:*) managed_prompt_tag='\[\033[1;33m\][TEST]\[\033[0m\]' ;;
            DEV:0) managed_prompt_tag='\[\033[1;30;42m\][DEV ROOT]\[\033[0m\]' ;;
            DEV:*) managed_prompt_tag='\[\033[1;32m\][DEV]\[\033[0m\]' ;;
            *) managed_prompt_tag='\[\033[1;37;45m\][UNTRUSTED]\[\033[0m\]' ;;
        esac
    else
        if (( EUID == 0 )); then
            managed_prompt_tag="[$managed_environment ROOT]"
        else
            managed_prompt_tag="[$managed_environment]"
        fi
    fi

    PS1="$managed_prompt_tag \\u@\\h:\\w$managed_prompt_character "
}

if [[ $- == *i* ]]; then
    managed_configure_environment_prompt /etc/managed-environment 0
fi
unset -f managed_configure_environment_prompt
