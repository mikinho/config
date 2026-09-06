#
# Author: Michael Welter <me@mikinho.com> - https://github.com/mikinho
#

# shellcheck shell=sh
# Read-only effective policy checks. Callers supply fail() and a reviewed CIDR file.

step_ca_normalize_ip_policy() {
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

step_ca_verify_effective_unit() {
    step_ca_networks=$1
    [ "$(systemctl show step-ca.service --property=Slice --value)" = system.slice ] \
        || fail "step-ca must run in the reviewed system.slice hierarchy"
    for step_ca_parent_slice in system.slice -.slice; do
        step_ca_parent_allow=$(systemctl show --property=IPAddressAllow --value -- "$step_ca_parent_slice") \
            || fail "cannot inspect the step-ca parent-slice IP allowlist"
        [ -z "$step_ca_parent_allow" ] \
            || fail "step-ca parent slices must not broaden IPAddressAllow"
    done
    step_ca_properties=$(systemctl show step-ca.service \
        --property=UMask --property=NoNewPrivileges --property=ProtectSystem \
        --property=ProtectHome --property=PrivateTmp --property=PrivateDevices \
        --property=ReadWritePaths --property=ReadOnlyPaths \
        --property=CapabilityBoundingSet --property=AmbientCapabilities \
        --property=MemoryDenyWriteExecute) \
        || fail "cannot inspect the effective step-ca unit"
    for step_ca_expected_property in \
        UMask=0077 NoNewPrivileges=yes ProtectSystem=strict ProtectHome=yes \
        PrivateTmp=yes PrivateDevices=yes ReadWritePaths=/var/lib/step-ca \
        ReadOnlyPaths=/etc/step-ca CapabilityBoundingSet= AmbientCapabilities= \
        MemoryDenyWriteExecute=yes
    do
        printf '%s\n' "$step_ca_properties" | grep -Fx "$step_ca_expected_property" >/dev/null \
            || fail "effective step-ca unit must set $step_ca_expected_property"
    done
    step_ca_expected_allow=$({ printf 'localhost\n'; cat "$step_ca_networks"; } | step_ca_normalize_ip_policy)
    [ -n "$step_ca_expected_allow" ] || fail "cannot normalize the selected step-ca client networks"
    step_ca_effective_allow=$(systemctl show step-ca.service --property=IPAddressAllow --value) \
        || fail "cannot inspect the effective step-ca IP allowlist"
    step_ca_effective_allow=$(printf '%s\n' "$step_ca_effective_allow" | step_ca_normalize_ip_policy)
    [ "$step_ca_effective_allow" = "$step_ca_expected_allow" ] \
        || fail "effective step-ca IP allowlist differs from selected client networks"
    step_ca_effective_deny=$(systemctl show step-ca.service --property=IPAddressDeny --value) \
        || fail "cannot inspect the effective step-ca IP denylist"
    step_ca_effective_deny=$(printf '%s\n' "$step_ca_effective_deny" | step_ca_normalize_ip_policy)
    step_ca_expected_deny=$(printf 'any\n' | step_ca_normalize_ip_policy)
    [ -n "$step_ca_expected_deny" ] || fail "cannot normalize the step-ca default-deny policy"
    [ "$step_ca_effective_deny" = "$step_ca_expected_deny" ] \
        || fail "effective step-ca unit must default-deny IPv4 and IPv6 addresses"
}
