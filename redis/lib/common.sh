#
# Author: Michael Welter <me@mikinho.com> - https://github.com/mikinho
#

# shellcheck shell=sh

# Shared validation and deterministic rendering for the Redis component.
# Callers define fail() before sourcing this file.

REDIS_APPLICATION_COMMAND_RULES='-@all +@read +@write +@transaction -@admin -@dangerous -keys -scan -randomkey -dbsize -vadd -vcard -vdim -vemb -vgetattr -vinfo -vlinks -vrandmember -vrem -vsetattr -vsim +eval +eval_ro +evalsha +evalsha_ro +publish +subscribe +unsubscribe +psubscribe +punsubscribe +ssubscribe +sunsubscribe +spublish +ping +echo +hello +quit +client|id +client|getname +client|setname +client|setinfo +client|info'
REDIS_LOCAL_TLS_PROBE_ADDRESS=127.0.0.1
REDIS_TLS_PORT=6379

redis_validate_minimum_tls_seconds() {
    # Empty selects the proportional default computed from the certificate.
    [ -n "$1" ] || return 0
    case "$1" in *[!0-9]* | 0*) fail "--minimum-tls-seconds must be a positive decimal integer" ;; esac
    [ "${#1}" -le 7 ] && [ "$1" -ge 300 ] && [ "$1" -le 2592000 ] \
        || fail "--minimum-tls-seconds must be between 300 and 2592000"
}

redis_describe_tls_margin() {
    if [ -n "$1" ]; then
        printf '%s seconds remaining' "$1"
    else
        printf 'the greater of 3600 seconds and one tenth of the certificate lifetime remaining'
    fi
}

# The two functions below are kept byte-identical in redis/lib/common.sh,
# mongodb/setup, mongodb/verify, and mongodb/lib/tls.sh; tests/mongodb enforces that.
certificate_time_epoch() {
    LC_ALL=C date -u -d "$1" +%s 2>/dev/null \
        || LC_ALL=C date -u -j -f '%b %e %T %Y %Z' "$1" +%s 2>/dev/null
}

# Acceptance margin for a certificate: an explicit --minimum-tls-seconds value,
# or by default the greater of one hour and one tenth of the certificate's own
# validity period. A 24-hour leaf must therefore hold 2.4 hours and a 90-day
# certificate 9 days, so one default serves short-lived and conventional
# issuance without treating a nearly expired long-lived certificate as healthy.
certificate_required_remaining_seconds() {
    certificate_file=$1
    explicit_seconds=$2
    if [ -n "$explicit_seconds" ]; then
        printf '%s\n' "$explicit_seconds"
        return
    fi
    certificate_bounds=$(LC_ALL=C openssl x509 -in "$certificate_file" -noout -startdate -enddate 2>/dev/null) \
        || fail "cannot read the certificate validity period: $certificate_file"
    not_before=$(printf '%s\n' "$certificate_bounds" | sed -n 's/^notBefore=//p')
    not_after=$(printf '%s\n' "$certificate_bounds" | sed -n 's/^notAfter=//p')
    not_before_epoch=$(certificate_time_epoch "$not_before") \
        || fail "cannot interpret the certificate notBefore time: $certificate_file"
    not_after_epoch=$(certificate_time_epoch "$not_after") \
        || fail "cannot interpret the certificate notAfter time: $certificate_file"
    certificate_lifetime=$((not_after_epoch - not_before_epoch))
    [ "$certificate_lifetime" -gt 0 ] \
        || fail "certificate validity period is empty or inverted: $certificate_file"
    proportional_seconds=$((certificate_lifetime / 10))
    if [ "$proportional_seconds" -gt 3600 ]; then
        printf '%s\n' "$proportional_seconds"
    else
        printf '3600\n'
    fi
}

# systemd expands localhost/any and may omit /32 on individual IPv4 addresses.
# Preserve all other tokens so an unexpected address cannot disappear silently.
redis_normalize_ip_policy() {
    awk '
        function address_value(address, octets, octet, value) {
            split(address, octets, "."); value = 0
            for (octet = 1; octet <= 4; octet++) value = value * 256 + octets[octet]
            return value
        }
        function emit(token, parts, prefix, size, first) {
            if (token == "localhost") { emit("127.0.0.0/8"); emit("::1/128"); return }
            if (token == "any") { emit("0.0.0.0/0"); emit("::/0"); return }
            if (token == "::1") token = "::1/128"
            split(token, parts, "/")
            if (parts[1] ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/ &&
                (parts[2] == "" || (parts[2] ~ /^[0-9]+$/ && parts[2] <= 32))) {
                prefix = parts[2] == "" ? 32 : parts[2]
                size = 2 ^ (32 - prefix)
                first = int(address_value(parts[1]) / size) * size
                printf "4 %.0f %.0f\n", first, first + size - 1
            } else print "6 " token
        }
        { for (field = 1; field <= NF; field++) emit($field) }
    ' | LC_ALL=C sort -k1,1 -k2,2n -k3,3n | awk '
        function flush() { if (active) printf "4 %.0f %.0f\n", first, last; active = 0 }
        $1 == "4" {
            if (active && $2 <= last + 1) { if ($3 > last) last = $3 }
            else { flush(); first = $2; last = $3; active = 1 }
            next
        }
        {
            flush()
            if ($2 == "::/0") ipv6_all = 1
            if ($0 != previous) ipv6[++ipv6_count] = $0
            previous = $0
        }
        END {
            flush()
            if (ipv6_all) print "6 ::/0"
            else for (entry = 1; entry <= ipv6_count; entry++) print ipv6[entry]
        }
    '
}

redis_verify_effective_systemd_policy() {
    redis_managed_policy=$1
    [ "$(systemctl show redis.service --property=Slice --value)" = system.slice ] \
        || fail "Redis must run in the reviewed system.slice hierarchy"
    for redis_parent_slice in system.slice -.slice; do
        redis_parent_allow=$(systemctl show --property=IPAddressAllow --value -- "$redis_parent_slice") \
            || fail "cannot inspect the Redis parent-slice IP allowlist"
        [ -z "$redis_parent_allow" ] \
            || fail "Redis parent slices must not broaden IPAddressAllow"
    done
    redis_expected_allow=$(awk -F= '$1 == "IPAddressAllow" { print $2 }' "$redis_managed_policy")
    [ -n "$redis_expected_allow" ] || fail "managed Redis unit has no allowed network policy"
    for redis_allowed_network in $redis_expected_allow; do
        case "$redis_allowed_network" in
            localhost) ;;
            */32) redis_validate_private_ipv4 "managed allowed address" "${redis_allowed_network%/32}" ;;
            *) fail "managed Redis allowlist must contain only localhost and exact private /32 addresses" ;;
        esac
    done
    redis_expected_allow=$(printf '%s\n' "$redis_expected_allow" | redis_normalize_ip_policy)
    [ -n "$redis_expected_allow" ] || fail "cannot normalize the managed Redis IP allowlist"
    redis_effective_allow=$(systemctl show redis.service --property=IPAddressAllow --value) \
        || fail "cannot read the effective Redis IP allowlist"
    redis_effective_allow=$(printf '%s\n' "$redis_effective_allow" | redis_normalize_ip_policy)
    [ "$redis_effective_allow" = "$redis_expected_allow" ] \
        || fail "effective Redis IP allowlist differs from the managed policy"
    redis_effective_deny=$(systemctl show redis.service --property=IPAddressDeny --value) \
        || fail "cannot read the effective Redis IP denylist"
    redis_effective_deny=$(printf '%s\n' "$redis_effective_deny" | redis_normalize_ip_policy)
    redis_expected_deny=$(printf 'any\n' | redis_normalize_ip_policy)
    [ -n "$redis_expected_deny" ] || fail "cannot normalize the Redis default-deny policy"
    [ "$redis_effective_deny" = "$redis_expected_deny" ] \
        || fail "effective Redis unit must default-deny IPv4 and IPv6 addresses"
    [ "$(systemctl show redis.service --property=UMask --value)" = 0077 ] \
        || fail "effective Redis unit must set UMask=0077"
}

redis_validate_safe_name() {
    redis_name_label=$1
    redis_name_value=$2
    [ -n "$redis_name_value" ] && [ "${#redis_name_value}" -le 64 ] \
        || fail "$redis_name_label must contain 1 to 64 characters"
    case "$redis_name_value" in
        -* | *[!A-Za-z0-9_.@-]*) fail "invalid $redis_name_label: $redis_name_value" ;;
    esac
}

redis_validate_prefix() {
    redis_prefix_label=$1
    redis_prefix_value=$2
    [ -n "$redis_prefix_value" ] && [ "${#redis_prefix_value}" -le 64 ] \
        || fail "$redis_prefix_label must contain 1 to 64 characters"
    case "$redis_prefix_value" in
        -* | *[!A-Za-z0-9_.-]*) fail "invalid $redis_prefix_label: $redis_prefix_value" ;;
    esac
}

redis_validate_absolute_path() {
    redis_path_label=$1
    redis_path_value=$2
    case "$redis_path_value" in
        /*) ;;
        *) fail "$redis_path_label must be an absolute path" ;;
    esac
    case "$redis_path_value" in
        *[!A-Za-z0-9_./-]*) fail "$redis_path_label contains unsupported characters: $redis_path_value" ;;
    esac
}

redis_validate_root_owned_parent_chain() {
    redis_parent_path=$(dirname -- "$1")
    while :; do
        [ -d "$redis_parent_path" ] && [ ! -L "$redis_parent_path" ] \
            || fail "path parent must be a real directory: $redis_parent_path"
        redis_parent_identity=$(stat -Lc '%U:%a' "$redis_parent_path" 2>/dev/null) \
            || fail "cannot inspect path parent: $redis_parent_path"
        redis_parent_owner=${redis_parent_identity%:*}
        redis_parent_mode=${redis_parent_identity##*:}
        [ "$redis_parent_owner" = root ] \
            || fail "path parent must be owned by root: $redis_parent_path"
        if [ "$((0$redis_parent_mode & 0022))" -ne 0 ]; then
            fail "path parent must not be writable by group or other: $redis_parent_path"
        fi
        [ "$redis_parent_path" != / ] || break
        redis_parent_path=$(dirname -- "$redis_parent_path")
    done
}

redis_validate_password_file() {
    redis_password_label=$1
    redis_password_path=$2
    redis_validate_absolute_path "$redis_password_label" "$redis_password_path"
    redis_validate_root_owned_parent_chain "$redis_password_path"
    [ -f "$redis_password_path" ] && [ ! -L "$redis_password_path" ] \
        || fail "$redis_password_label must be a regular, non-symbolic-link file"
    redis_password_identity=$(stat -Lc '%U:%G:%a:%h' "$redis_password_path" 2>/dev/null) \
        || fail "cannot inspect $redis_password_label: $redis_password_path"
    [ "$redis_password_identity" = root:root:600:1 ] \
        || fail "$redis_password_label must be a one-link root:root mode 0600 file"
    awk 'NR > 1 { exit 1 }' "$redis_password_path" \
        || fail "$redis_password_label must contain exactly one line"
    [ -s "$redis_password_path" ] || fail "$redis_password_label must not be empty"
}

redis_validate_runtime_password_file() {
    redis_password_label=$1
    redis_password_path=$2
    redis_validate_absolute_path "$redis_password_label" "$redis_password_path"
    redis_validate_root_owned_parent_chain "$redis_password_path"
    [ -f "$redis_password_path" ] && [ ! -L "$redis_password_path" ] \
        || fail "$redis_password_label must be a regular, non-symbolic-link file"
    redis_password_identity=$(stat -Lc '%U:%G:%a:%h' "$redis_password_path" 2>/dev/null) \
        || fail "cannot inspect $redis_password_label: $redis_password_path"
    case "$redis_password_identity" in
        root:root:400:1 | root:root:600:1) ;;
        *) fail "$redis_password_label must be a one-link root:root mode 0400 or 0600 file" ;;
    esac
    awk 'NR > 1 { exit 1 }' "$redis_password_path" \
        || fail "$redis_password_label must contain exactly one line"
    [ -s "$redis_password_path" ] || fail "$redis_password_label must not be empty"
}

redis_read_password() {
    redis_password_path=$1
    redis_password_value=$(cat -- "$redis_password_path")
    [ -n "$redis_password_value" ] \
        || fail "password file resolved to an empty value: $redis_password_path"
    [ "${#redis_password_value}" -ge 32 ] \
        || fail "password must contain at least 32 characters: $redis_password_path"
    [ "${#redis_password_value}" -le 1024 ] \
        || fail "password must not exceed 1024 characters: $redis_password_path"
    printf '%s' "$redis_password_value"
}

redis_password_hash() {
    redis_hash_password=$1
    printf '%s' "$redis_hash_password" | sha256sum | awk '{ print $1 }'
}

redis_validate_maxmemory_mib() {
    case "$1" in
        '' | *[!0-9]*) fail "--maxmemory-mib must be an integer" ;;
    esac
    [ "$1" -ge 64 ] && [ "$1" -le 1048576 ] \
        || fail "--maxmemory-mib must be between 64 and 1048576"
}

redis_validate_data_profile() {
    case "$1" in
        cache | durable) ;;
        *) fail "--data-profile must be cache or durable" ;;
    esac
}

redis_validate_private_ipv4() {
    redis_ipv4_label=$1
    redis_ipv4_value=$2
    printf '%s\n' "$redis_ipv4_value" | awk -F. '
        NF != 4 { exit 1 }
        {
            for (octet_index = 1; octet_index <= 4; octet_index += 1) {
                if ($octet_index !~ /^[0-9]+$/ || $octet_index < 0 || $octet_index > 255) {
                    exit 1
                }
                if (length($octet_index) > 1 && substr($octet_index, 1, 1) == "0") {
                    exit 1
                }
            }
        }
    ' || fail "$redis_ipv4_label must be one explicit IPv4 address"
    case "$redis_ipv4_value" in
        10.* | 192.168.*) ;;
        172.*)
            redis_second_octet=$(printf '%s\n' "$redis_ipv4_value" | awk -F. '{ print $2 }')
            [ "$redis_second_octet" -ge 16 ] && [ "$redis_second_octet" -le 31 ] \
                || fail "$redis_ipv4_label must be an RFC 1918 private address"
            ;;
        *) fail "$redis_ipv4_label must be an RFC 1918 private address" ;;
    esac
}

redis_validate_server_host() {
    redis_server_host=$1
    [ -n "$redis_server_host" ] && [ "${#redis_server_host}" -le 253 ] \
        || fail "--server-host must contain 1 to 253 characters"
    printf '%s\n' "$redis_server_host" | awk -F. '
        NF < 2 || $0 !~ /[A-Za-z]/ { exit 1 }
        {
            for (label_index = 1; label_index <= NF; label_index += 1) {
                if ($label_index !~ /^[A-Za-z0-9-]+$/ \
                    || $label_index !~ /^[A-Za-z0-9]/ \
                    || $label_index !~ /[A-Za-z0-9]$/ \
                    || length($label_index) > 63) {
                    exit 1
                }
            }
        }
    ' || fail "--server-host must be a stable DNS name with at least two labels"
}

redis_admin_acl_line() {
    redis_acl_admin_user=$1
    redis_acl_admin_hash=$2
    printf 'user %s reset on #%s ~* &* +@all\n' \
        "$redis_acl_admin_user" "$redis_acl_admin_hash"
}

redis_application_acl_line() {
    redis_acl_application_user=$1
    redis_acl_application_hash=$2
    redis_acl_key_prefix=$3
    redis_acl_channel_prefix=$4
    printf 'user %s reset on #%s resetkeys ~%s:* resetchannels &%s:* %s\n' \
        "$redis_acl_application_user" "$redis_acl_application_hash" \
        "$redis_acl_key_prefix" "$redis_acl_channel_prefix" \
        "$REDIS_APPLICATION_COMMAND_RULES"
}

redis_render_configuration() {
    redis_render_model=$1
    redis_render_data_profile=$2
    redis_render_template=$3
    redis_render_destination=$4
    redis_render_maxmemory=$5
    redis_render_bind=${6:-}
    redis_render_cert=${7:-}
    redis_render_key=${8:-}
    redis_render_ca=${9:-}

    awk \
        -v maxmemory="$redis_render_maxmemory" \
        -v bind_address="$redis_render_bind" \
        -v certificate_file="$redis_render_cert" \
        -v private_key_file="$redis_render_key" \
        -v ca_file="$redis_render_ca" '
        {
            gsub(/@MAXMEMORY_MIB@/, maxmemory)
            gsub(/@BIND_ADDRESS@/, bind_address)
            gsub(/@TLS_CERTIFICATE_FILE@/, certificate_file)
            gsub(/@TLS_PRIVATE_KEY_FILE@/, private_key_file)
            gsub(/@TLS_CA_FILE@/, ca_file)
            print
        }
    ' "$redis_render_template" > "$redis_render_destination"

    if grep -Eq '@[A-Z0-9_]+@' "$redis_render_destination"; then
        fail "rendered Redis configuration contains an unresolved placeholder"
    fi

    case "$redis_render_model" in
        local)
            grep -Fxc 'bind 127.0.0.1' "$redis_render_destination" >/dev/null \
                || fail "rendered local Redis configuration has an invalid listener"
            ;;
        network)
            grep -Fxc 'port 0' "$redis_render_destination" >/dev/null \
                || fail "rendered network Redis configuration did not disable plaintext TCP"
            ;;
        *) fail "internal error: unsupported Redis model: $redis_render_model" ;;
    esac

    case "$redis_render_data_profile" in
        cache)
            grep -Fxc 'maxmemory-policy allkeys-lru' "$redis_render_destination" >/dev/null \
                || fail "rendered cache configuration must use allkeys-lru"
            grep -Fxc 'save ""' "$redis_render_destination" >/dev/null \
                || fail "rendered cache configuration must disable RDB schedules"
            grep -Fxc 'appendonly no' "$redis_render_destination" >/dev/null \
                || fail "rendered cache configuration must disable AOF"
            ;;
        durable)
            grep -Fxc 'maxmemory-policy noeviction' "$redis_render_destination" >/dev/null \
                || fail "rendered durable configuration must use noeviction"
            grep -Fxc 'appendonly yes' "$redis_render_destination" >/dev/null \
                || fail "rendered durable configuration must enable AOF"
            grep -Fxc 'appendfsync everysec' "$redis_render_destination" >/dev/null \
                || fail "rendered durable configuration must use AOF everysec"
            ;;
        *) fail "internal error: unsupported Redis data profile: $redis_render_data_profile" ;;
    esac
}

redis_render_systemd_policy() {
    redis_systemd_template=$1
    redis_systemd_allowed_file=$2
    redis_systemd_destination=$3

    awk -v allowed_file="$redis_systemd_allowed_file" '
        $0 == "@IP_ADDRESS_ALLOW@" {
            print "IPAddressAllow=localhost"
            while ((getline allowed_address < allowed_file) > 0) {
                print "IPAddressAllow=" allowed_address "/32"
            }
            close(allowed_file)
            next
        }
        { print }
    ' "$redis_systemd_template" > "$redis_systemd_destination"
    if grep -Eq '@[A-Z0-9_]+@' "$redis_systemd_destination"; then
        fail "rendered Redis systemd policy contains an unresolved placeholder"
    fi
}

redis_verify_local_tls_service_identity() {
    redis_served_server_host=$1
    redis_served_ca_file=$2
    redis_served_expected_certificate=$3
    redis_served_transcript=$4
    redis_served_leaf_certificate=$5

    if ! openssl s_client \
        -connect "$REDIS_LOCAL_TLS_PROBE_ADDRESS:$REDIS_TLS_PORT" \
        -servername "$redis_served_server_host" \
        -CAfile "$redis_served_ca_file" \
        -verify_hostname "$redis_served_server_host" \
        -verify_return_error -showcerts \
        < /dev/null > "$redis_served_transcript" 2>&1
    then
        fail "actively served TLS chain and hostname verification failed"
    fi
    awk '
        /-----BEGIN CERTIFICATE-----/ && !capturing { capturing = 1 }
        capturing { print }
        capturing && /-----END CERTIFICATE-----/ { exit }
    ' "$redis_served_transcript" > "$redis_served_leaf_certificate"
    openssl x509 -in "$redis_served_leaf_certificate" -noout >/dev/null 2>&1 \
        || fail "actively served TLS endpoint did not present a leaf certificate"
    openssl x509 -checkhost "$redis_served_server_host" -noout \
        -in "$redis_served_leaf_certificate" >/dev/null \
        || fail "actively served TLS certificate does not identify --server-host"
    redis_expected_certificate_fingerprint=$(openssl x509 \
        -in "$redis_served_expected_certificate" -noout -fingerprint -sha256 2>/dev/null) \
        || fail "cannot fingerprint configured TLS certificate"
    redis_served_certificate_fingerprint=$(openssl x509 \
        -in "$redis_served_leaf_certificate" -noout -fingerprint -sha256 2>/dev/null) \
        || fail "cannot fingerprint actively served TLS certificate"
    [ "$redis_served_certificate_fingerprint" = "$redis_expected_certificate_fingerprint" ] \
        || fail "actively served TLS certificate differs from configured certificate"
}
