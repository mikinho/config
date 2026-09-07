#!/usr/bin/env sh

#
# Author: Michael Welter <me@mikinho.com> - https://github.com/mikinho
#

# Validation shared by the certificate-reload entry point. Certificate lifetime
# calculations stay byte-identical to setup and verify. Caller supplies fail().

validate_safe_name() {
    setup_label=$1
    setup_value=$2
    [ -n "$setup_value" ] && [ "${#setup_value}" -le 64 ] \
        || fail "$setup_label must contain 1 to 64 characters"
    case "$setup_value" in
        -* | *[!A-Za-z0-9_.@-]*) fail "invalid $setup_label: $setup_value" ;;
    esac
}

validate_absolute_path() {
    setup_label=$1
    setup_path=$2
    case "$setup_path" in
        /*) ;;
        *) fail "$setup_label must be an absolute path" ;;
    esac
    case "$setup_path" in
        *[!A-Za-z0-9_./-]*) fail "$setup_label contains unsupported characters: $setup_path" ;;
    esac
}

validate_root_owned_parent_chain() {
    parent_path=$(dirname -- "$1")
    while :; do
        [ -d "$parent_path" ] && [ ! -L "$parent_path" ] \
            || fail "path parent must be a real directory: $parent_path"
        parent_identity=$(stat -Lc '%U:%a' "$parent_path" 2>/dev/null) \
            || fail "cannot inspect path parent: $parent_path"
        parent_owner=${parent_identity%:*}
        parent_mode=${parent_identity##*:}
        [ "$parent_owner" = root ] || fail "path parent must be owned by root: $parent_path"
        if [ "$((0$parent_mode & 0022))" -ne 0 ]; then
            fail "path parent must not be writable by group or other: $parent_path"
        fi
        [ "$parent_path" != / ] || break
        parent_path=$(dirname -- "$parent_path")
    done
}

validate_ipv4_address() {
    printf '%s\n' "$BIND_ADDRESS" | awk -F. '
        NF != 4 { exit 1 }
        {
            for (octet_index = 1; octet_index <= 4; octet_index += 1) {
                if ($octet_index !~ /^[0-9]+$/ || $octet_index < 0 || $octet_index > 255) {
                    exit 1
                }
            }
        }
    ' || fail "--bind-address must be one explicit IPv4 address"
    case "$BIND_ADDRESS" in
        10.* | 192.168.*) ;;
        172.*)
            second_octet=$(printf '%s\n' "$BIND_ADDRESS" | awk -F. '{ print $2 }')
            [ "$second_octet" -ge 16 ] && [ "$second_octet" -le 31 ] \
                || fail "--bind-address must be an RFC 1918 private address"
            ;;
        *) fail "--bind-address must be an RFC 1918 private address" ;;
    esac
}

validate_member_host() {
    [ -n "$MEMBER_HOST" ] && [ "${#MEMBER_HOST}" -le 253 ] \
        || fail "--member-host must contain 1 to 253 characters"
    printf '%s\n' "$MEMBER_HOST" | awk -F. '
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
    ' || fail "--member-host must be a stable DNS name with at least two labels"
}

certificate_time_epoch() {
    LC_ALL=C date -u -d "$1" +%s 2>/dev/null \
        || LC_ALL=C date -u -j -f '%b %e %T %Y %Z' "$1" +%s 2>/dev/null
}

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

validate_tls_server_identity() {
    [ -f "$TLS_CERTIFICATE_KEY_FILE" ] && [ ! -L "$TLS_CERTIFICATE_KEY_FILE" ] \
        || fail "TLS certificate/key file must be a regular, non-symbolic-link file"
    [ -f "$TLS_CA_FILE" ] && [ ! -L "$TLS_CA_FILE" ] \
        || fail "TLS CA file must be a regular, non-symbolic-link file"
    validate_root_owned_parent_chain "$TLS_CERTIFICATE_KEY_FILE"
    validate_root_owned_parent_chain "$TLS_CA_FILE"
    [ "$(stat -Lc '%U:%G:%a:%h' "$TLS_CERTIFICATE_KEY_FILE")" = mongod:mongod:400:1 ] \
        || fail "TLS certificate/key file must be a one-link mongod:mongod mode 0400 file"
    [ "$(stat -Lc '%U:%G:%a:%h' "$TLS_CA_FILE")" = root:root:644:1 ] \
        || fail "TLS CA file must be a one-link root:root mode 0644 file"
    openssl verify -purpose sslserver -CAfile "$TLS_CA_FILE" \
        -untrusted "$TLS_CERTIFICATE_KEY_FILE" \
        "$TLS_CERTIFICATE_KEY_FILE" >/dev/null \
        || fail "TLS server certificate does not validate against the selected CA"
    openssl x509 -in "$TLS_CERTIFICATE_KEY_FILE" -pubkey -noout \
        > "$TEMPORARY_DIRECTORY/tls-certificate-public-key.pem" \
        || fail "cannot read the TLS server certificate"
    openssl pkey -in "$TLS_CERTIFICATE_KEY_FILE" -passin pass: -pubout \
        -out "$TEMPORARY_DIRECTORY/tls-private-public-key.pem" \
        || fail "TLS server private key must be readable without a passphrase"
    cmp -s \
        "$TEMPORARY_DIRECTORY/tls-certificate-public-key.pem" \
        "$TEMPORARY_DIRECTORY/tls-private-public-key.pem" \
        || fail "TLS server certificate and private key do not match"
    openssl x509 -in "$TLS_CERTIFICATE_KEY_FILE" -noout -checkhost "$MEMBER_HOST" >/dev/null \
        || fail "TLS certificate does not cover member host $MEMBER_HOST"
    required_tls_seconds=$(certificate_required_remaining_seconds "$TLS_CERTIFICATE_KEY_FILE" "$TLS_MINIMUM_REMAINING_SECONDS") \
        || exit 1
    openssl x509 -in "$TLS_CERTIFICATE_KEY_FILE" -noout \
        -checkend "$required_tls_seconds" >/dev/null \
        || fail "TLS server certificate has less than $required_tls_seconds seconds remaining"
}
