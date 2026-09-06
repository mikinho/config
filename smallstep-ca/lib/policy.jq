# Shared issuance policy for setup, installed verification, and negative tests.
# Provisioner overrides may narrow, but never expand, the selected authority.

def seconds:
    if type != "string" or (test("^[1-9][0-9]*(s|m|h)$") | not) then null
    else capture("^(?<value>[1-9][0-9]*)(?<unit>s|m|h)$") |
        (.value | tonumber) * (if .unit == "h" then 3600 elif .unit == "m" then 60 else 1 end)
    end;

def names:
    type == "object" and length > 0 and
    all(to_entries[];
        ((.key == "cn" or .key == "dns" or .key == "email" or .key == "ip" or .key == "uri") and
        (.value | type == "array" and length > 0 and all(.[]; type == "string" and length > 0))));

def public_key:
    type == "object" and keys == ["alg", "crv", "kid", "kty", "use", "x", "y"] and
    .use == "sig" and .kty == "EC" and .crv == "P-256" and .alg == "ES256" and
    (.kid | type == "string" and length > 0 and length <= 128) and
    all(.x, .y; type == "string" and test("^[A-Za-z0-9_-]{42}[AEIMQUYcgkosw048]$"));

def restricted_claims:
    type == "object" and
    (keys - ["minTLSCertDuration", "defaultTLSCertDuration", "maxTLSCertDuration",
        "disableRenewal", "allowRenewalAfterExpiry", "disableSmallstepExtensions"] | length == 0) and
    ((has("disableRenewal") | not) or (.disableRenewal | type == "boolean")) and
    ((has("allowRenewalAfterExpiry") | not) or .allowRenewalAfterExpiry == false) and
    ((has("disableSmallstepExtensions") | not) or .disableSmallstepExtensions == false) and
    all(to_entries[] | select(.key | endswith("TLSCertDuration")); (.value | seconds) != null) and
    (((.minTLSCertDuration // "5m") | seconds) as $minimum |
    ((.defaultTLSCertDuration // ($default_hours | tostring) + "h") | seconds) as $default |
    ((.maxTLSCertDuration // ($max_hours | tostring) + "h") | seconds) as $maximum |
    $minimum != null and $default != null and $maximum != null and
    $minimum >= 300 and $minimum <= $default and $default <= $maximum and
    $default <= ($default_hours * 3600) and $maximum <= ($max_hours * 3600));

($default_hours | type == "number" and floor == . and . > 0 and . <= 168) and
($max_hours | type == "number" and floor == . and . >= $default_hours and . <= 840) and
(.authority.policy | type == "object" and keys == ["x509"]) and
(.authority.policy.x509 | type == "object" and
    (keys - ["allow", "allowWildcardNames", "deny"] | length == 0) and
    (.allow | names) and
    ((has("deny") | not) or (.deny | names)) and
    .allowWildcardNames == false) and
(.authority.provisioners | type == "array" and length > 0 and
    (map(.name) | unique | length) == length and
    (map(.key.kid) | unique | length) == length and
    all(.[];
        type == "object" and
        (keys - ["type", "name", "key", "encryptedKey", "claims"] | length == 0) and
        .type == "JWK" and (.name | type == "string" and length > 0 and length <= 128) and
        (.key | public_key) and
        (.encryptedKey | type == "string" and test("^[A-Za-z0-9_-]+(\\.[A-Za-z0-9_-]+){4}$")) and
        ((has("claims") | not) or (.claims | restricted_claims))))
