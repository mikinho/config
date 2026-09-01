#!/usr/bin/env sh

#
# Author: Michael Welter <me@mikinho.com> - https://github.com/mikinho
#

# Shared Monit version policy. EPEL 9 currently provides the preferred release,
# while the supported EPEL 10.2 stream remains on the compatibility release.
MONIT_SUPPORTED_VERSION=5.35.2
MONIT_PREFERRED_VERSION=6.0.0

monit_validate_version_policy() {
    monit_version_is_at_least \
        "$MONIT_PREFERRED_VERSION" "$MONIT_SUPPORTED_VERSION"
}

monit_version_is_at_least() {
    monit_candidate_version=$1
    monit_minimum_version=$2

    awk \
        -v candidate="$monit_candidate_version" \
        -v minimum="$monit_minimum_version" '
        BEGIN {
            if (candidate !~ /^[0-9]+([.][0-9]+)*$/ \
                    || minimum !~ /^[0-9]+([.][0-9]+)*$/) {
                exit 2
            }

            candidate_count = split(candidate, candidate_parts, ".")
            minimum_count = split(minimum, minimum_parts, ".")
            component_count = candidate_count > minimum_count \
                ? candidate_count : minimum_count

            for (component = 1; component <= component_count; component += 1) {
                candidate_value = component <= candidate_count \
                    ? candidate_parts[component] + 0 : 0
                minimum_value = component <= minimum_count \
                    ? minimum_parts[component] + 0 : 0
                if (candidate_value > minimum_value) {
                    exit 0
                }
                if (candidate_value < minimum_value) {
                    exit 1
                }
            }
            exit 0
        }
    '
}

monit_read_binary_version() {
    monit_version_binary=$1

    LC_ALL=C "$monit_version_binary" -V 2>&1 | awk '
        $1 == "This" && $2 == "is" && $3 == "Monit" && $4 == "version" {
            print $5
            exit
        }
    '
}
