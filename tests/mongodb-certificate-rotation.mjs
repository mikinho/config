/** Behavioral checks for least-privilege MongoDB TLS adoption and recovery guards. */
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import vm from "node:vm";

const sourceRoot = new URL("../mongodb/", import.meta.url);
const scriptSource = readFileSync(new URL("certificate-rotation.js", sourceRoot), "utf8");
const reloadSource = readFileSync(new URL("reload-tls", sourceRoot), "utf8");
const librarySource = readFileSync(new URL("lib/tls.sh", sourceRoot), "utf8");
const setupSource = readFileSync(new URL("setup", sourceRoot), "utf8");
const verifySource = readFileSync(new URL("verify", sourceRoot), "utf8");
const privilege = () => [{ resource: { cluster: true }, actions: ["rotateCertificates"] }];
const selections = [
    "--rotation-user", "rotation_user", "--rotation-role", "rotation_role",
    "--rotation-password-file", "/root/rotation.password", "--replica-set", "example-rs",
    "--bind-address", "10.20.30.40", "--member-host", "mongodb.internal.example",
    "--tls-certificate-key-file", "/etc/pki/mongodb/server.pem",
    "--tls-ca-file", "/etc/pki/mongodb/ca.pem", "--minimum-tls-seconds", "3600",
];

test("shared validators match setup and verify", () => {
    const extract = (source, name) => {
        const expression = new RegExp(`^${name}\\(\\) \\{[\\s\\S]*?^\\}`, "m");
        const match = source.match(expression);
        assert.ok(match, `missing validator: ${name}`);
        return match[0];
    };
    for (const name of ["validate_safe_name", "validate_absolute_path", "validate_root_owned_parent_chain",
        "validate_ipv4_address", "validate_member_host"]) {
        const reference = extract(setupSource, name);
        assert.equal(extract(librarySource, name), reference, `lib/tls.sh drifted: ${name}`);
        // verify uses its own scratch-variable prefix and IPv4 function name.
        const verifyName = name === "validate_ipv4_address" ? "validate_private_ipv4" : name;
        const copy = extract(verifySource, verifyName)
            .replaceAll("verify_label", "setup_label").replaceAll("verify_value", "setup_value")
            .replaceAll("verify_path", "setup_path").replace("validate_private_ipv4()", "validate_ipv4_address()");
        assert.equal(copy, reference, `verify drifted: ${name}`);
    }
});

/** Execute the actual shell-side JavaScript with an isolated MongoDB API. */
function executeOperation(options = {}) {
    const calls = [];
    const output = [];
    const authValues = [];
    let helloCount = 0;
    let exit = 0;
    const secret = options.secret ?? " private fixture password ";
    const admin = {
        auth(value) { authValues.push(value); return options.authResult ?? { ok: 1 }; },
        createRole(value) { calls.push({ createRole: value }); },
        createUser(value) { calls.push({ createUser: value }); },
        runCommand(command) {
            calls.push(command);
            if (command.usersInfo) return { ok: options.usersOk ?? 1, users: options.existingUser ? [{}] : [] };
            if (command.rolesInfo) return { ok: options.rolesOk ?? 1, roles: options.existingRole ? [options.existingRole] : [] };
            if (command.connectionStatus) return { ok: 1, authInfo: {
                authenticatedUserRoles: options.roles ?? [{ role: "rotation_role", db: "admin" }],
                authenticatedUserPrivileges: options.privileges ?? privilege(),
            } };
            if (command.rotateCertificates) {
                if (options.throwRotation) throw new Error(options.driverMessage ?? `driver leaked ${secret}`);
                return { ok: options.rotationOk ?? 1 };
            }
            if (command.hello) {
                helloCount += 1;
                const connectionValue = options.changedConnection ? helloCount : 42;
                return { ok: 1, isWritablePrimary: options.primary ?? true,
                    setName: options.replicaSet ?? "example-rs",
                    connectionId: { toString: () => String(connectionValue) } };
            }
            throw new Error("Unexpected mocked command");
        },
    };
    vm.runInNewContext(scriptSource, {
        require: () => ({
            lstatSync: () => ({ isFile: () => true, uid: 0, nlink: options.links ?? 1, mode: options.mode ?? 0o600 }),
            readFileSync: () => `${secret}\n`,
        }),
        db: { getSiblingDB: () => admin },
        process: { env: {
            MONGODB_ROTATION_MODE: options.operation ?? "rotate", MONGODB_ROTATION_USER: "rotation_user",
            MONGODB_ROTATION_ROLE: "rotation_role", MONGODB_ROTATION_PASSWORD_FILE: "/credential",
            MONGODB_ADMIN_USER: "admin_user", MONGODB_ADMIN_PASSWORD_FILE: "/admin-credential",
            MONGODB_REPLICA_SET: "example-rs",
        } },
        print: (value) => output.push(value), quit: (value) => { exit = value; },
    });
    assert.ok(!output.join("\n").includes(secret));
    return { calls, output, exit, authValues };
}

test("rotation preserves password whitespace and compares BSON connection IDs by value", () => {
    const result = executeOperation();
    assert.equal(result.exit, 0);
    assert.equal(result.authValues[0].pwd, " private fixture password ");
    assert.equal(result.calls.filter(call => call.rotateCertificates).length, 1);
});

test("numeric authentication and systemd credential permissions are supported", () => {
    assert.equal(executeOperation({ authResult: 1, mode: 0o400 }).exit, 0);
});

test("a verification run never invokes rotation", () => {
    const result = executeOperation({ operation: "verify" });
    assert.equal(result.exit, 0);
    assert.equal(result.calls.filter(call => call.rotateCertificates).length, 0);
});

test("failed authentication, unsafe modes, hard links, and multiline credentials are rejected", () => {
    for (const options of [{ authResult: 0 }, { mode: 0o644 }, { mode: 0o4600 }, { links: 2 }, { secret: "one\ntwo" }]) {
        const result = executeOperation(options);
        assert.equal(result.exit, 1);
        assert.equal(result.calls.filter(call => call.rotateCertificates).length, 0);
    }
});

test("broader roles, actions, or resources are rejected before rotation", () => {
    for (const options of [
        { roles: [{ role: "root", db: "admin" }] },
        { privileges: [{ resource: { cluster: true }, actions: ["rotateCertificates", "shutdown"] }] },
        { privileges: [{ resource: { anyResource: true }, actions: ["rotateCertificates"] }] },
    ]) {
        const result = executeOperation(options);
        assert.equal(result.exit, 1);
        assert.equal(result.calls.filter(call => call.rotateCertificates).length, 0);
    }
});

test("the wrong replica set and a non-primary cannot rotate or create identities", () => {
    for (const operation of ["rotate", "create"]) {
        for (const options of [{ replicaSet: "different-rs" }, { primary: false }]) {
            const result = executeOperation({ ...options, operation });
            assert.equal(result.exit, 1);
            assert.ok(result.calls.every(call => !call.rotateCertificates && !call.createUser && !call.createRole));
        }
    }
});

test("rotation failures and a replaced connection cannot be reported as success", () => {
    for (const options of [{ rotationOk: 0 }, { throwRotation: true }, { changedConnection: true }]) {
        assert.equal(executeOperation(options).exit, 1);
    }
});

test("fixed validation failures identify the failed check without exposing credentials", () => {
    for (const [options, diagnostic] of [
        [{ mode: 0o644 }, "Unsafe credential file"],
        [{ secret: "one\ntwo" }, "Invalid credential contents"],
        [{ operation: "unknown" }, "Incomplete rotation selection"],
        [{ operation: "create", authResult: 0 }, "Administrative authentication failed"],
        [{ primary: false }, "Unexpected replica-set primary"],
        [{ operation: "create", usersOk: 0 }, "Rotation user already exists or cannot be inspected"],
        [{ operation: "create", rolesOk: 0 }, "Role inspection failed"],
        [{ operation: "create", existingRole: { roles: [], privileges: [] } }, "Existing role has unexpected privileges"],
        [{ authResult: 0 }, "Rotation authentication failed"],
        [{ privileges: [] }, "Rotation account is not restricted to the expected privilege"],
        [{ rotationOk: 0 }, "Certificate rotation failed"],
        [{ changedConnection: true }, "Connection continuity verification failed"],
    ]) {
        const result = executeOperation(options);
        assert.equal(result.exit, 1);
        assert.deepEqual(result.output, [`MongoDB certificate operation failed; ${diagnostic}.`]);
    }
});

test("driver diagnostics stay suppressed even when they contain an allowed message", () => {
    for (const driverMessage of [undefined,
        "Certificate rotation failed: driver leaked private fixture password",
        "Certificate rotation failed\nconnection details",
    ]) {
        const result = executeOperation({ throwRotation: true, driverMessage });
        assert.equal(result.exit, 1);
        assert.deepEqual(result.output, ["MongoDB certificate operation failed; credential and driver details suppressed."]);
    }
});

test("creation grants exactly one custom action and SCRAM-SHA-256", () => {
    const result = executeOperation({ operation: "create" });
    assert.equal(result.exit, 0);
    const role = result.calls.find(call => call.createRole).createRole;
    const user = result.calls.find(call => call.createUser).createUser;
    assert.equal(JSON.stringify(role.privileges), JSON.stringify(privilege()));
    assert.equal(JSON.stringify(user.mechanisms), JSON.stringify(["SCRAM-SHA-256"]));
    assert.equal(JSON.stringify(user.roles), JSON.stringify([{ role: "rotation_role", db: "admin" }]));
});

test("creation refuses existing users and broader existing roles", () => {
    for (const options of [{ existingUser: true }, { existingRole: {
        roles: [], privileges: [{ resource: { cluster: true }, actions: ["shutdown"] }],
    } }]) {
        const result = executeOperation({ ...options, operation: "create" });
        assert.equal(result.exit, 1);
        assert.ok(result.calls.every(call => !call.createUser && !call.createRole));
    }
});

test("creation can reuse an exactly matching custom role without updating it", () => {
    const result = executeOperation({ operation: "create", existingRole: { roles: [], privileges: privilege() } });
    assert.equal(result.exit, 0);
    assert.equal(result.calls.filter(call => call.createRole).length, 0);
    assert.equal(result.calls.filter(call => call.createUser).length, 1);
});

test("plans validate selections without reading credentials", () => {
    const result = spawnSync("sh", [new URL("reload-tls", sourceRoot).pathname, "--plan", ...selections], { encoding: "utf8" });
    assert.equal(result.status, 0, result.stderr);
    assert.match(result.stdout, /no password file was read/);
    for (const extra of [["--bind-address", "203.0.113.1"], ["--member-host", "localhost"],
        ["--minimum-tls-seconds", "0"], ["--admin-user", "unexpected"], ["--check"], ["--unknown"]]) {
        const invalid = spawnSync("sh", [new URL("reload-tls", sourceRoot).pathname, "--plan", ...selections, ...extra], { encoding: "utf8" });
        assert.notEqual(invalid.status, 0);
    }
});

/** Run the complete shell helper with OS commands isolated in a temporary PATH. */
function withRuntime(callback) {
    const root = realpathSync(mkdtempSync(join(tmpdir(), "mongodb-reload-runtime-")));
    const bin = join(root, "sbin");
    const lib = join(root, "lib");
    mkdirSync(bin); mkdirSync(lib);
    const command = (name, text) => writeFileSync(join(bin, name), `#!/bin/sh\nset -eu\n${text}\n`, { mode: 0o755 });
    const file = (name, text) => { const path = join(root, name); writeFileSync(path, text); return path; };
    const certificate = file("server.pem", "staged fixture");
    const ca = file("ca.pem", "public CA fixture");
    const credential = file("rotation.password", "private fixture");
    const adminCredential = file("admin.password", "private admin fixture");
    const config = file("mongod.conf", `net:\n  port: 27017\n  bindIp: 127.0.0.1,10.20.30.40\n  tls:\n    mode: requireTLS\n    certificateKeyFile: ${certificate}\n    CAFile: ${ca}\n`);
    writeFileSync(join(lib, "tls.sh"), librarySource);
    writeFileSync(join(lib, "certificate-rotation.js"), scriptSource);
    const source = reloadSource.replaceAll("/usr/local/sbin", bin)
        .replaceAll("/usr/local/libexec/config-mongodb", lib)
        .replace("CONFIGURATION_PATH=/etc/mongod.conf", `CONFIGURATION_PATH=${config}`);
    writeFileSync(join(bin, "reload-tls"), source, { mode: 0o755 });
    command("id", "echo 0");
    command("stat", `for path do :; done
        if [ -d "$path" ]; then case "$*" in *%U:%G:%a*) echo "\${SOURCE_PARENT_IDENTITY:-root:root:755}";; *) echo root:755;; esac
        else case "$path" in
            *server.pem) echo mongod:mongod:400:1;;
            *.password) echo root:root:\${PASSWORD_MODE:-600}:1;;
            *) echo "\${SOURCE_FILE_IDENTITY:-root:root:644:1}";;
        esac; fi`);
    command("mongod", 'printf "db version %s\\n" "${MONGODB_VERSION:-v8.0.29}"');
    command("getent", "echo '10.20.30.40 STREAM fixture'");
    command("ip", "echo '1: eth0 inet 10.20.30.40/24 scope global eth0'");
    command("systemctl", `case "$*" in
        is-active*) exit 0;;
        show*) if [ -f "$TEST_ROOT/rotated" ] && [ "\${PID_CHANGE:-0}" = 1 ]; then echo 43; else echo 42; fi;;
        *) echo 'Unexpected service mutation' >&2; exit 99;;
    esac`);
    command("timeout", "shift; exec \"$@\"");
    command("openssl", `case "$1" in
        verify) exit "\${STAGED_INVALID:-0}";;
        s_client)
            echo "$*" >> "$TEST_ROOT/handshakes"
            [ "\${TLS_FAIL:-0}" = 0 ] || exit 1
            echo 'served fixture';;
        pkey) for arg do if [ "\${previous:-}" = -out ]; then echo public > "$arg"; fi; previous=$arg; done;;
        x509)
            case "$*" in
                *-checkend*|*-checkhost*) exit 0;;
                *-pubkey*) echo public;;
                *served.pem*) if [ -f "$TEST_ROOT/rotated" ] && [ "\${KEEP_STALE:-0}" = 0 ]; then echo new;
                    else echo "\${LIVE_FINGERPRINT:-old}"; fi;;
                *) echo new;;
            esac;;
        *) exit 98;;
    esac`);
    command("mongosh", `echo "$MONGODB_ROTATION_MODE" >> "$TEST_ROOT/operations"
        echo "$*" >> "$TEST_ROOT/arguments"
        [ "$NODE_OPTIONS" = --jitless ] || exit 97
        case "$*" in *AllowInvalid*|*tlsInsecure*) exit 96;; esac
        if [ "$MONGODB_ROTATION_MODE" = rotate ]; then
            [ "\${ROTATION_FAIL:-0}" = 0 ] || exit 3
            touch "$TEST_ROOT/rotated"
        fi`);
    const args = selections.map(value => ({ "/root/rotation.password": credential,
        "/etc/pki/mongodb/server.pem": certificate, "/etc/pki/mongodb/ca.pem": ca })[value] ?? value);
    const run = (environment = {}, extraArgs = []) => spawnSync("sh", [join(bin, "reload-tls"), ...args, ...extraArgs], {
        encoding: "utf8", env: { ...process.env, PATH: `${bin}:${process.env.PATH}`, TEST_ROOT: root, ...environment },
    });
    try { callback({ root, run, adminCredential, certificate }); }
    finally { rmSync(root, { recursive: true, force: true }); }
}

test("runtime rotates a stale leaf and verifies two strict TLS handshakes without writing certificates", () => withRuntime(({ root, run, certificate }) => {
    const result = run();
    assert.equal(result.status, 0, result.stderr);
    assert.equal(readFileSync(join(root, "operations"), "utf8"), "rotate\n");
    const handshakes = readFileSync(join(root, "handshakes"), "utf8").trim().split("\n");
    assert.equal(handshakes.length, 2);
    assert.ok(handshakes.every(line => line.includes("-verify_return_error") && line.includes("-verify_hostname")));
    assert.equal(readFileSync(certificate, "utf8"), "staged fixture");
    assert.ok(!readFileSync(join(root, "arguments"), "utf8").includes("private fixture"));
}));

test("MongoDB 8.0 patch upgrades still adopt renewed certificates", () => withRuntime(({ root, run }) => {
    const result = run({ MONGODB_VERSION: "v8.0.30" });
    assert.equal(result.status, 0, result.stderr);
    assert.equal(readFileSync(join(root, "operations"), "utf8"), "rotate\n");
}));

test("unsupported MongoDB series stop before TLS handshakes or database operations", () => {
    for (const version of ["v7.0.29", "v8.1.0", "v8.2.0", "v9.0.0", "unknown"]) {
        withRuntime(({ root, run }) => {
            const result = run({ MONGODB_VERSION: version });
            assert.notEqual(result.status, 0);
            assert.match(result.stderr, /MongoDB 8\.0 LTS is required/);
            assert.equal(existsSync(join(root, "handshakes")), false);
            assert.equal(existsSync(join(root, "operations")), false);
        });
    }
});

test("unsafe executable sources direct operators to the installed helper", () => {
    for (const environment of [
        { SOURCE_PARENT_IDENTITY: "operator:operator:755" },
        { SOURCE_PARENT_IDENTITY: "root:root:775" },
        { SOURCE_FILE_IDENTITY: "operator:operator:644:1" },
    ]) {
        withRuntime(({ root, run }) => {
            const result = run(environment);
            assert.notEqual(result.status, 0);
            assert.match(result.stderr, /use the installed .*\/reload-mongodb-tls for apply mode/);
            assert.equal(existsSync(join(root, "operations")), false);
        });
    }
});

test("matching live certificate verifies the account but skips rotation", () => withRuntime(({ root, run }) => {
    const result = run({ LIVE_FINGERPRINT: "new" });
    assert.equal(result.status, 0, result.stderr);
    assert.equal(readFileSync(join(root, "operations"), "utf8"), "verify\n");
}));

test("runtime creation uses a fresh verification process", () => withRuntime(({ root, run, adminCredential }) => {
    const result = run({}, ["--create-user", "--admin-user", "admin_user", "--admin-password-file", adminCredential]);
    assert.equal(result.status, 0, result.stderr);
    assert.equal(readFileSync(join(root, "operations"), "utf8"), "create\nverify\n");
}));

test("invalid staged or live TLS and unsafe credentials stop before MongoDB commands", () => {
    for (const env of [{ STAGED_INVALID: "1" }, { TLS_FAIL: "1" }, { PASSWORD_MODE: "644" }]) {
        withRuntime(({ root, run }) => {
            assert.notEqual(run(env).status, 0);
            assert.equal(existsSync(join(root, "operations")), false);
        });
    }
});

test("failed rotation, stale post-rotation leaf, and PID change remain failed operations", () => {
    for (const env of [{ ROTATION_FAIL: "1" }, { KEEP_STALE: "1" }, { PID_CHANGE: "1" }]) {
        withRuntime(({ run, certificate }) => {
            assert.notEqual(run(env).status, 0);
            assert.equal(readFileSync(certificate, "utf8"), "staged fixture");
        });
    }
});
