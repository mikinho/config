# deploy/profiles/baseline.profile

#
# Author: Michael Welter <me@mikinho.com> - https://github.com/mikinho
#

# Required by nginx.conf and the provided default sites.
stubs/http/quic.conf
stubs/http/ratelimit.conf
stubs/http/logging.conf
stubs/http/tls.conf
stubs/http/upstream-fallback.conf
