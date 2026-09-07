// Pinned step-ca integration: validate before startup, confirm the public API
// contains no private JWK material, and exercise certificate lifetime bounds.
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { randomBytes, X509Certificate } from "node:crypto";
import { chmodSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import https from "node:https";
import net from "node:net";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { setTimeout as delay } from "node:timers/promises";

const repository = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const validator = join(repository, "smallstep-ca/validate-policy");
const expectedVersions = [["step", ["version"], "Smallstep CLI/0.30.6"], ["step-ca", ["--version"], "Smallstep CA/0.30.2"]];
if (process.argv.includes("--check")) {
    for (const file of [validator, join(repository, "smallstep-ca/lib/policy.jq")]) readFileSync(file);
    process.stdout.write("Validated Smallstep runtime-test dependencies.\n");
    process.exit(0);
}
for (const [command, arguments_, expected] of expectedVersions) {
    const result = spawnSync(command, arguments_, { encoding: "utf8" });
    assert.equal(result.status, 0, `Install pinned ${command} before running the CA integration test.`);
    assert.ok(result.stdout.startsWith(expected), `The CA integration test requires ${expected}.`);
}

const temporaryDirectory = mkdtempSync(join(tmpdir(), "config-step-ca-runtime-"));
chmodSync(temporaryDirectory, 0o700);
const environment = { ...process.env, STEPPATH: temporaryDirectory };
let ca;

/** Require normal fixture-command completion before interpreting its exit status. */
function execute(command, arguments_) {
    const result = spawnSync(command, arguments_, { env: environment, encoding: "utf8", timeout: 30000 });
    assert.equal(result.error, undefined, `Fixture ${command} command could not complete normally`);
    assert.equal(result.signal, null, `Fixture ${command} command was terminated by a signal`);
    return result;
}

/** Reserve a transient loopback port without touching installed services. */
async function selectPort() {
    const server = net.createServer();
    await new Promise((resolve_, reject) => server.listen(0, "127.0.0.1", resolve_).on("error", reject));
    const { port } = server.address();
    await new Promise((resolve_) => server.close(resolve_));
    return port;
}

/** Read one certificate-verified endpoint without client authentication. */
function request(port, rootCertificate, path) {
    return new Promise((resolve_, reject) => {
        const outgoing = https.get({ hostname: "127.0.0.1", port, path, ca: rootCertificate }, (response) => {
            let data = "";
            response.on("data", (chunk) => { data += chunk; });
            response.on("end", () => {
                try { resolve_(JSON.parse(data)); } catch (error) { reject(error); }
            });
        });
        outgoing.setTimeout(2000, () => outgoing.destroy(new Error("CA probe timed out")));
        outgoing.on("error", reject);
    });
}

try {
    const port = await selectPort();
    const passwordFile = join(temporaryDirectory, "password");
    writeFileSync(passwordFile, `${randomBytes(32).toString("hex")}\n`, { mode: 0o600 });
    const initialization = execute("step", ["ca", "init", "--deployment-type", "standalone", "--name", "Disposable Test CA",
        "--dns", "127.0.0.1", "--address", `127.0.0.1:${port}`, "--provisioner", "runtime-fixture", "--password-file", passwordFile]);
    assert.equal(initialization.status, 0, "Could not initialize disposable CA");
    const configurationFile = join(temporaryDirectory, "config/ca.json");
    const configuration = JSON.parse(readFileSync(configurationFile, "utf8"));
    configuration.authority.claims = { minTLSCertDuration: "5m", defaultTLSCertDuration: "1h", maxTLSCertDuration: "2h" };
    configuration.authority.policy = { x509: { allow: { dns: ["audit.internal.example.invalid"] }, allowWildcardNames: false } };
    writeFileSync(configurationFile, JSON.stringify(configuration), { mode: 0o600 });
    const validated = execute(validator, ["--configuration-file", configurationFile, "--default-tls-hours", "1", "--max-tls-hours", "2"]);
    assert.equal(validated.status, 0, validated.stderr);
    // Keep the CA's stderr so a startup failure explains itself instead of
    // surfacing only as "exited before readiness".
    let caDiagnostics = "";
    ca = spawn("step-ca", [configurationFile, "--password-file", passwordFile], {
        env: environment,
        stdio: ["ignore", "ignore", "pipe"],
    });
    ca.stderr.setEncoding("utf8");
    ca.stderr.on("data", (chunk) => { caDiagnostics = `${caDiagnostics}${chunk}`.slice(-4096); });
    const rootPath = join(temporaryDirectory, "certs/root_ca.crt");
    const rootCertificate = readFileSync(rootPath);
    let response;
    for (let attempt = 0; attempt < 100; attempt++) {
        assert.equal(ca.exitCode, null, `Fixture CA exited before readiness: ${caDiagnostics}`);
        try { response = await request(port, rootCertificate, "/provisioners"); break; } catch { await delay(100); }
    }
    assert.ok(response, `Fixture CA did not become ready: ${caDiagnostics}`);
    // An empty list would satisfy every() vacuously; the disclosure check only
    // means something once the fixture provisioner is actually listed.
    assert.ok(Array.isArray(response.provisioners) && response.provisioners.length >= 1,
        "Public provisioner endpoint listed no provisioners");
    assert.ok(response.provisioners.some(({ name }) => name === "runtime-fixture"),
        "Public provisioner endpoint did not list the fixture provisioner");
    assert.ok(response.provisioners.every(({ key }) => key && !Object.hasOwn(key, "d")), "Public provisioner endpoint exposed a private key");
    for (const hours of [1, 2, 3]) {
        const certificatePath = join(temporaryDirectory, `leaf-${hours}.crt`);
        const issuance = execute("step", ["ca", "certificate", "audit.internal.example.invalid", certificatePath,
            join(temporaryDirectory, `leaf-${hours}.key`), "--ca-url", `https://127.0.0.1:${port}`, "--root", rootPath,
            "--provisioner", "runtime-fixture", "--provisioner-password-file", passwordFile, "--not-after", `${hours}h`]);
        assert.equal(issuance.status, hours <= 2 ? 0 : 1, `Unexpected ${hours}-hour certificate issuance result`);
        if (hours <= 2) {
            const certificate = new X509Certificate(readFileSync(certificatePath));
            const duration = Date.parse(certificate.validTo) - Date.parse(certificate.validFrom);
            assert.ok(Math.abs(duration - hours * 3600000) <= 61000, "Issued lifetime exceeds expected backdating tolerance");
        } else {
            assert.match(issuance.stderr, /validity|duration|maximum|notAfter/iu, "Excessive issuance failed for an unexpected reason");
        }
    }
    process.stdout.write("Validated pinned CA public-key disclosure prevention and below/at/above-limit certificate issuance.\n");
} finally {
    if (ca && ca.exitCode === null) {
        ca.kill("SIGTERM");
        await Promise.race([new Promise((resolve_) => ca.once("exit", resolve_)), delay(5000).then(() => ca.kill("SIGKILL"))]);
    }
    rmSync(temporaryDirectory, { recursive: true, force: true });
}
