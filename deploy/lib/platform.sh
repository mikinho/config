#!/usr/bin/env sh

#
# Author: Michael Welter <me@mikinho.com> - https://github.com/mikinho
#

# Shared supported-platform and EPEL installation helpers. This file is sourced
# by repository entry points; callers must validate that it is a regular,
# non-symbolic-link repository file before sourcing it.

# These values are public to the sourcing entry point.
# shellcheck disable=SC2034
PLATFORM_DEFAULT_OS_RELEASE=/etc/os-release
PLATFORM_EPEL_RELEASE_BASE_URL=https://dl.fedoraproject.org/pub/epel
PLATFORM_MODE=${PLATFORM_MODE:-install}
PLATFORM_OS_ID=
PLATFORM_OS_NAME=
PLATFORM_OS_VERSION_MAJOR=
PLATFORM_OS_ARCHITECTURE=
PLATFORM_OS_LABEL=
PLATFORM_RHEL_REPOSITORY_LISTING=
PLATFORM_RHEL_REPOSITORY_LISTING_STATE=unknown

platform_fail() {
    printf '%s: %s\n' "${PROGRAM_NAME:-platform}" "$*" >&2
    exit 1
}

platform_print_command() {
    printf '+'
    for platform_command_argument do
        printf ' %s' "$platform_command_argument"
    done
    printf '\n'
}

platform_run() {
    if [ "$PLATFORM_MODE" = plan ]; then
        platform_print_command "$@"
    else
        "$@"
    fi
}

platform_read_os_release_value() {
    platform_release_file=$1
    platform_release_key=$2

    awk -F= -v requested_key="$platform_release_key" '
        $1 == requested_key {
            sub(/^[^=]*=/, "")
            print
            exit
        }
    ' "$platform_release_file"
}

platform_strip_os_release_quotes() {
    platform_release_value=$1

    case "$platform_release_value" in
        \"*\")
            platform_release_value=${platform_release_value#\"}
            platform_release_value=${platform_release_value%\"}
            ;;
        \'*\')
            platform_release_value=${platform_release_value#\'}
            platform_release_value=${platform_release_value%\'}
            ;;
    esac

    printf '%s\n' "$platform_release_value"
}

platform_detect_architecture() {
    platform_detected_architecture=$(uname -m)
    case "$platform_detected_architecture" in
        x86_64 | amd64) PLATFORM_OS_ARCHITECTURE=x86_64 ;;
        aarch64 | arm64) PLATFORM_OS_ARCHITECTURE=aarch64 ;;
        *) platform_fail "unsupported host architecture: $platform_detected_architecture" ;;
    esac
}

platform_detect_supported_os() {
    platform_os_release_file=$1
    # /etc/os-release is commonly a relative link to /usr/lib/os-release.
    # -f follows valid links while rejecting broken links and non-files.
    [ -f "$platform_os_release_file" ] \
        || platform_fail "OS release metadata must be a regular file: $platform_os_release_file"

    PLATFORM_OS_ID=$(platform_strip_os_release_quotes \
        "$(platform_read_os_release_value "$platform_os_release_file" ID)")
    PLATFORM_OS_NAME=$(platform_strip_os_release_quotes \
        "$(platform_read_os_release_value "$platform_os_release_file" PRETTY_NAME)")
    platform_release_version=$(platform_strip_os_release_quotes \
        "$(platform_read_os_release_value "$platform_os_release_file" VERSION_ID)")
    PLATFORM_OS_VERSION_MAJOR=${platform_release_version%%.*}

    case "$PLATFORM_OS_VERSION_MAJOR" in
        9 | 10) ;;
        *)
            platform_fail \
                "unsupported OS version in $platform_os_release_file: ${platform_release_version:-missing VERSION_ID}"
            ;;
    esac

    case "$PLATFORM_OS_ID" in
        rhel)
            PLATFORM_OS_LABEL="Red Hat Enterprise Linux $PLATFORM_OS_VERSION_MAJOR"
            ;;
        rocky)
            PLATFORM_OS_LABEL="Rocky Linux $PLATFORM_OS_VERSION_MAJOR"
            ;;
        centos)
            case "$PLATFORM_OS_NAME" in
                *CentOS*Stream*) ;;
                *)
                    platform_fail \
                        "CentOS Stream is required; detected: ${PLATFORM_OS_NAME:-unknown CentOS variant}"
                    ;;
            esac
            # shellcheck disable=SC2034
            PLATFORM_OS_LABEL="CentOS Stream $PLATFORM_OS_VERSION_MAJOR"
            ;;
        *) platform_fail "unsupported base OS: ${PLATFORM_OS_ID:-missing ID}" ;;
    esac

    platform_detect_architecture
}

platform_select_rhel_builder_repository() {
    platform_repository_listing=$1
    platform_requested_state=$2
    platform_requested_transport=$3
    platform_builder_prefix="codeready-builder-for-rhel-$PLATFORM_OS_VERSION_MAJOR-$PLATFORM_OS_ARCHITECTURE"
    platform_generic_rhui_builder_prefix="codeready-builder-for-rhel-$PLATFORM_OS_VERSION_MAJOR"

    printf '%s\n' "$platform_repository_listing" | awk \
        -v builder_prefix="$platform_builder_prefix" \
        -v generic_rhui_builder_prefix="$platform_generic_rhui_builder_prefix" \
        -v requested_state="$platform_requested_state" \
        -v requested_transport="$platform_requested_transport" '
        function is_builder(repository_id) {
            return repository_id == builder_prefix "-rpms" \
                || repository_id == builder_prefix "-rhui-rpms" \
                || repository_id == generic_rhui_builder_prefix "-rhui-rpms"
        }

        is_builder($1) \
            && (requested_state == "any" || $NF == requested_state) \
            && (requested_transport == "any" \
                || $1 != builder_prefix "-rpms") {
            print $1
            exit
        }
    '
}

platform_select_enabled_rhel_extended_repository() {
    platform_repository_listing=$1

    printf '%s\n' "$platform_repository_listing" | awk '
        $NF == "enabled" \
            && ($1 ~ /-eus(-rhui)?-rpms$/ \
                || $1 ~ /-e4s(-rhui)?-rpms$/) {
            print $1
            exit
        }
    '
}

platform_load_rhel_repository_listing() {
    [ "$PLATFORM_RHEL_REPOSITORY_LISTING_STATE" = unknown ] || return 0

    PLATFORM_RHEL_REPOSITORY_LISTING_STATE=unavailable
    if command -v dnf >/dev/null 2>&1; then
        PLATFORM_RHEL_REPOSITORY_LISTING=$(LC_ALL=C dnf -q repolist --all) \
            || platform_fail "cannot inspect configured RHEL repositories"
        PLATFORM_RHEL_REPOSITORY_LISTING_STATE=available
    elif [ "$PLATFORM_MODE" = install ]; then
        platform_fail "dnf is required to inspect configured RHEL repositories"
    fi

    return 0
}

platform_assert_rhel_epel_stream_supported() {
    platform_load_rhel_repository_listing
    [ "$PLATFORM_RHEL_REPOSITORY_LISTING_STATE" = available ] || return 0

    platform_extended_repository=$(
        platform_select_enabled_rhel_extended_repository \
            "$PLATFORM_RHEL_REPOSITORY_LISTING"
    )
    [ -z "$platform_extended_repository" ] || platform_fail \
        "this installer does not support rolling EPEL with a pinned RHEL extended-update stream (enabled: $platform_extended_repository); switch to current non-EUS RHEL repositories (non-EUS RHUI on Azure) or use a reviewed package source built for this minor release"
}

platform_has_rhel_rhui_repository() {
    platform_repository_listing=$1
    platform_rhui_prefix="rhel-$PLATFORM_OS_VERSION_MAJOR-"

    printf '%s\n' "$platform_repository_listing" | awk \
        -v rhui_prefix="$platform_rhui_prefix" '
        index($1, rhui_prefix) == 1 && $1 ~ /-rhui-rpms$/ {
            found = 1
            exit
        }
        END { exit found ? 0 : 1 }
    '
}

platform_enable_rhel_builder_repository() {
    platform_load_rhel_repository_listing

    if [ "$PLATFORM_RHEL_REPOSITORY_LISTING_STATE" = available ]; then
        platform_builder_repository=$(platform_select_rhel_builder_repository \
            "$PLATFORM_RHEL_REPOSITORY_LISTING" enabled any)
        if [ -n "$platform_builder_repository" ]; then
            printf 'CodeReady Builder repository already enabled: %s\n' \
                "$platform_builder_repository"
            return
        fi

        platform_builder_repository=$(platform_select_rhel_builder_repository \
            "$PLATFORM_RHEL_REPOSITORY_LISTING" any rhui)
        if [ -n "$platform_builder_repository" ]; then
            platform_run dnf config-manager --set-enabled \
                "$platform_builder_repository"
            return
        fi

        if platform_has_rhel_rhui_repository \
            "$PLATFORM_RHEL_REPOSITORY_LISTING"; then
            platform_fail \
                "CodeReady Builder repository is unavailable through configured RHEL RHUI repositories"
        fi
    fi

    if [ "$PLATFORM_MODE" = install ]; then
        command -v subscription-manager >/dev/null 2>&1 \
            || platform_fail \
                "subscription-manager is required to enable CodeReady Builder on RHEL"
    fi
    platform_run subscription-manager repos --enable \
        "codeready-builder-for-rhel-$PLATFORM_OS_VERSION_MAJOR-$PLATFORM_OS_ARCHITECTURE-rpms"
}

platform_install_epel_repository() {
    case "$PLATFORM_OS_ID" in
        rhel) platform_assert_rhel_epel_stream_supported ;;
        rocky | centos) ;;
        *)
            platform_fail "platform detection must run before EPEL installation"
            ;;
    esac

    platform_run dnf install --assumeyes ca-certificates dnf-plugins-core

    case "$PLATFORM_OS_ID" in
        rhel) platform_enable_rhel_builder_repository ;;
        rocky | centos)
            platform_run dnf config-manager --set-enabled crb
            ;;
        *) platform_fail "platform detection must run before EPEL installation" ;;
    esac

    platform_run dnf install --assumeyes \
        "$PLATFORM_EPEL_RELEASE_BASE_URL/epel-release-latest-$PLATFORM_OS_VERSION_MAJOR.noarch.rpm"
}
