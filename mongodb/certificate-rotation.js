/* global db, print, quit */

// Run only through reload-tls. Passwords are read from protected files; neither
// credential-bearing URIs nor password values appear in argv or journal output.
(() => {
    const fs = require("node:fs");
    const environment = process.env;
    const succeeded = (result) => result === 1 || result?.ok === 1;
    const user = environment.MONGODB_ROTATION_USER;
    const role = environment.MONGODB_ROTATION_ROLE;
    const mode = environment.MONGODB_ROTATION_MODE;
    const expectedPrivileges = [{ resource: { cluster: true }, actions: ["rotateCertificates"] }];
    // Only exact, fixed diagnostics from our checks may reach operator logs.
    // Driver and filesystem errors can contain credentials or connection data.
    const safeDiagnostics = new Set([
        "Unsafe credential file",
        "Invalid credential contents",
        "Incomplete rotation selection",
        "Administrative authentication failed",
        "Unexpected replica-set primary",
        "Rotation user already exists or cannot be inspected",
        "Role inspection failed",
        "Existing role has unexpected privileges",
        "Rotation authentication failed",
        "Rotation account is not restricted to the expected privilege",
        "Certificate rotation failed",
        "Connection continuity verification failed",
    ]);

    const readPassword = (path) => {
        const identity = fs.lstatSync(path);
        if (!identity.isFile() || identity.uid !== 0 || identity.nlink !== 1 ||
            ![0o400, 0o600].includes(identity.mode & 0o7777)) {
            throw new Error("Unsafe credential file");
        }
        const value = fs.readFileSync(path, "utf8").replace(/\r?\n$/, "");
        if (!value || value.length > 1024 || /[\r\n\0]/.test(value)) {
            throw new Error("Invalid credential contents");
        }
        return value;
    };

    const exactPrivileges = (privileges) => privileges?.length === 1 &&
        privileges[0].resource?.cluster === true &&
        Object.keys(privileges[0].resource).length === 1 &&
        privileges[0].actions?.length === 1 && privileges[0].actions[0] === "rotateCertificates";

    try {
        if (!["create", "verify", "rotate"].includes(mode) || !user || !role ||
            !environment.MONGODB_REPLICA_SET) throw new Error("Incomplete rotation selection");
        const admin = db.getSiblingDB("admin");
        const password = readPassword(environment.MONGODB_ROTATION_PASSWORD_FILE);
        if (mode === "create") {
            if (!succeeded(admin.auth({
                user: environment.MONGODB_ADMIN_USER,
                pwd: readPassword(environment.MONGODB_ADMIN_PASSWORD_FILE),
                mechanism: "SCRAM-SHA-256",
            }))) throw new Error("Administrative authentication failed");
            const primary = admin.runCommand({ hello: 1 });
            if (primary.ok !== 1 || !primary.isWritablePrimary ||
                primary.setName !== environment.MONGODB_REPLICA_SET) {
                throw new Error("Unexpected replica-set primary");
            }
            const users = admin.runCommand({ usersInfo: { user, db: "admin" } });
            if (users.ok !== 1 || users.users.length) throw new Error("Rotation user already exists or cannot be inspected");
            const roles = admin.runCommand({ rolesInfo: { role, db: "admin" }, showPrivileges: true });
            if (roles.ok !== 1 || roles.roles.length > 1) throw new Error("Role inspection failed");
            if (roles.roles.length) {
                if (roles.roles[0].roles.length || !exactPrivileges(roles.roles[0].privileges)) {
                    throw new Error("Existing role has unexpected privileges");
                }
            } else {
                admin.createRole({ role, privileges: expectedPrivileges, roles: [] });
            }
            admin.createUser({ user, pwd: password, mechanisms: ["SCRAM-SHA-256"],
                roles: [{ role, db: "admin" }] });
            print("Created the dedicated certificate-rotation identity; fresh-session verification follows.");
            return;
        }

        if (!succeeded(admin.auth({ user, pwd: password, mechanism: "SCRAM-SHA-256" }))) {
            throw new Error("Rotation authentication failed");
        }
        const status = admin.runCommand({ connectionStatus: 1, showPrivileges: true });
        const roles = status.authInfo?.authenticatedUserRoles;
        if (status.ok !== 1 || roles?.length !== 1 || roles[0].role !== role || roles[0].db !== "admin" ||
            !exactPrivileges(status.authInfo.authenticatedUserPrivileges)) {
            throw new Error("Rotation account is not restricted to the expected privilege");
        }
        const before = admin.runCommand({ hello: 1 });
        if (before.ok !== 1 || !before.isWritablePrimary ||
            before.setName !== environment.MONGODB_REPLICA_SET || before.connectionId == null) {
            throw new Error("Unexpected replica-set primary");
        }
        if (mode === "rotate") {
            const rotated = admin.runCommand({ rotateCertificates: 1,
                message: "Adopt validated renewed TLS files without restarting the database" });
            if (rotated.ok !== 1) throw new Error("Certificate rotation failed");
            const after = admin.runCommand({ hello: 1 });
            if (after.ok !== 1 || !after.isWritablePrimary || after.setName !== before.setName ||
                String(after.connectionId) !== String(before.connectionId)) {
                throw new Error("Connection continuity verification failed");
            }
        }
        print(JSON.stringify({ rotationIdentityVerified: true,
            rotated: mode === "rotate", connectionPreserved: true }));
    } catch (error) {
        const diagnostic = safeDiagnostics.has(error?.message)
            ? error.message : "credential and driver details suppressed";
        print(`MongoDB certificate operation failed; ${diagnostic}.`);
        quit(1);
    }
})();
