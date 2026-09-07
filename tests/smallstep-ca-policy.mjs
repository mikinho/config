// Exercise the shared deployment validator with real P-256 keys and unsafe
// provisioner inputs. All keys are disposable and remain in a private directory.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { generateKeyPairSync } from "node:crypto";
import { chmodSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const repository = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const validator = join(repository, "smallstep-ca/validate-policy");
const temporaryDirectory = mkdtempSync(join(tmpdir(), "config-step-ca-policy-tests-"));
chmodSync(temporaryDirectory, 0o700);
const fixturePath = join(temporaryDirectory, "config.json");
const { privateKey, publicKey } = generateKeyPairSync("ec", { namedCurve: "P-256" });
const keyMetadata = { kid: "fixture-key", use: "sig", alg: "ES256" };
const publicJwk = { ...publicKey.export({ format: "jwk" }), ...keyMetadata };
const baseline = {
    authority: {
        policy: {
            x509: {
                allow: { dns: ["*.internal.example.invalid"] },
                allowWildcardNames: false,
            },
        },
        provisioners: [{
            type: "JWK",
            name: "audit-fixture",
            key: publicJwk,
            encryptedKey: "protected.encrypted.iv.ciphertext.tag",
        }],
    },
};

const privateJwk = { ...privateKey.export({ format: "jwk" }), ...keyMetadata };
// Every schema or policy violation is reported by the shared jq gate with one
// diagnostic; only the curve-membership check runs later, in OpenSSL.
const POLICY_DIAGNOSTIC = "issuance policy requires public-only P-256 JWKs";
const CURVE_DIAGNOSTIC = "provisioner public key is not a valid P-256 curve point";

/**
 * Run the validator on a fixture. `expectedDiagnostic` is null for an accepted
 * fixture, otherwise text the rejection must carry. A validator that could not
 * run, was killed, or exited with an unexpected status is a test error, never a
 * passing negative case. Key material must not appear in any output.
 */
function validateFixture(fixture, expectedDiagnostic, description) {
    writeFileSync(fixturePath, JSON.stringify(fixture), { mode: 0o600 });
    const result = spawnSync(validator, ["--configuration-file", fixturePath], {
        encoding: "utf8",
        timeout: 10000,
    });
    assert.equal(result.error, undefined, `${description}: validator could not run: ${result.error}`);
    assert.equal(result.signal, null, `${description}: validator was terminated by ${result.signal}`);
    for (const secret of [privateJwk.d, publicJwk.x, publicJwk.y]) {
        assert.ok(!result.stdout.includes(secret) && !result.stderr.includes(secret),
            `${description}: validator output disclosed key material`);
    }
    if (expectedDiagnostic === null) {
        assert.equal(result.status, 0, `${description}: ${result.stderr}`);
        return;
    }
    assert.equal(result.status, 1, `${description}: expected a rejection, got status ${result.status}: ${result.stderr}`);
    assert.ok(result.stderr.includes(expectedDiagnostic),
        `${description}: rejected for an unexpected reason: ${result.stderr}`);
}

/** Apply a single mutation to an otherwise valid baseline and require the stated diagnostic. */
function rejectMutation(description, mutate, expectedDiagnostic = POLICY_DIAGNOSTIC) {
    const fixture = structuredClone(baseline);
    mutate(fixture.authority.provisioners[0], fixture);
    validateFixture(fixture, expectedDiagnostic, description);
}

try {
    validateFixture(baseline, null, "valid public P-256 key");
    const narrowed = structuredClone(baseline);
    narrowed.authority.provisioners[0].claims = {
        minTLSCertDuration: "10m",
        defaultTLSCertDuration: "12h",
        maxTLSCertDuration: "24h",
        disableRenewal: true,
        allowRenewalAfterExpiry: false,
        disableSmallstepExtensions: false,
    };
    validateFixture(narrowed, null, "claims narrow the authority limits");
    rejectMutation("private signing scalar", (provisioner) => {
        provisioner.key = { ...privateKey.export({ format: "jwk" }), ...keyMetadata };
    });
    rejectMutation("off-curve public point", (provisioner) => {
        provisioner.key.x = Buffer.alloc(32).toString("base64url");
        provisioner.key.y = Buffer.alloc(32).toString("base64url");
    }, CURVE_DIAGNOSTIC);
    rejectMutation("noncanonical coordinate encoding", (provisioner) => {
        provisioner.key.x = `${provisioner.key.x.slice(0, -1)}B`;
    });
    rejectMutation("unapproved key field", (provisioner) => { provisioner.key.x5u = "https://example.invalid"; });
    rejectMutation("one-year lifetime override", (provisioner) => {
        provisioner.claims = { maxTLSCertDuration: "8760h" };
    });
    rejectMutation("longer default lifetime", (provisioner) => {
        provisioner.claims = { defaultTLSCertDuration: "48h" };
    });
    rejectMutation("invalid effective lifetime interval", (provisioner) => {
        provisioner.claims = { maxTLSCertDuration: "12h" };
    });
    rejectMutation("expired-certificate renewal", (provisioner) => {
        provisioner.claims = { allowRenewalAfterExpiry: true };
    });
    rejectMutation("null duration", (provisioner) => { provisioner.claims = { maxTLSCertDuration: null }; });
    rejectMutation("unknown duration units", (provisioner) => { provisioner.claims = { maxTLSCertDuration: "1d" }; });
    rejectMutation("SSH enabling claim", (provisioner) => { provisioner.claims = { enableSSHCA: true }; });
    rejectMutation("provisioner template options", (provisioner) => {
        provisioner.options = { x509: { template: "unreviewed" } };
    });
    rejectMutation("plaintext provisioner password", (provisioner) => { provisioner.password = "fixture"; });
    rejectMutation("duplicate provisioner identity", (provisioner, fixture) => {
        fixture.authority.provisioners.push(structuredClone(provisioner));
    });
    rejectMutation("wildcard certificate issuance", (_provisioner, fixture) => {
        fixture.authority.policy.x509.allowWildcardNames = true;
    });
    // Setup's separate-file path must enforce the same validator.
    const provisionersPath = join(temporaryDirectory, "provisioners.json");
    const policyPath = join(temporaryDirectory, "policy.json");
    writeFileSync(provisionersPath, JSON.stringify(baseline.authority.provisioners), { mode: 0o600 });
    writeFileSync(policyPath, JSON.stringify(baseline.authority.policy), { mode: 0o600 });
    const separate = spawnSync(validator, ["--provisioners-file", provisionersPath, "--x509-policy-file", policyPath], {
        encoding: "utf8",
        timeout: 10000,
    });
    assert.equal(separate.error, undefined, `separate policy inputs: validator could not run: ${separate.error}`);
    assert.equal(separate.status, 0, separate.stderr);
    writeFileSync(fixturePath, `${JSON.stringify(baseline)}\n${JSON.stringify(baseline)}\n`, { mode: 0o600 });
    const multiple = spawnSync(validator, ["--configuration-file", fixturePath], { encoding: "utf8", timeout: 10000 });
    assert.equal(multiple.error, undefined, `multiple values: validator could not run: ${multiple.error}`);
    assert.equal(multiple.status, 1, "multiple JSON configuration values must be rejected");
    assert.ok(multiple.stderr.includes("cannot parse the CA issuance policy"), multiple.stderr);
    for (const entryPoint of ["setup", "verify"]) {
        assert.match(readFileSync(join(repository, "smallstep-ca", entryPoint), "utf8"), /"\$POLICY_VALIDATOR"/u);
    }
    process.stdout.write("Validated CA public keys, curve membership, restricted claims, and shared setup/verify policy.\n");
} finally {
    rmSync(temporaryDirectory, { recursive: true, force: true });
}
