# config

## Optional Node.js Cache-Control fallback

The shared nginx configuration provides an opt-in `$cache_control_fallback`
for applications that serve static files through nginx and proxy other
responses to Node.js.

The map is not loaded globally because its `$static_cache_control` input is
owned by the application. Loading it when no participating application is
installed would make `nginx -t` fail with an unknown-variable error.

Define the application policy at `http {}` scope, then include the fallback
map exactly once:

```nginx
map $uri $static_cache_control {
    "~*[._-][a-f0-9]{8,}\.[a-z0-9]+$" "public, max-age=31536000, immutable";
    default                              "public, max-age=3600";
}

include includes/cache-control-fallback.conf;
```

The application can then apply the result at the same configuration scope as
its other response headers:

```nginx
add_header Cache-Control $cache_control_fallback always;
```

For a response served by nginx, the application's static policy is emitted.
For a proxied response, the fallback is empty, allowing the Node.js
application's own `Cache-Control` header to pass through without duplication.
