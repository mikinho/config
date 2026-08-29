/* global db */

// Verifies the exact backup identity, authentication mechanism, primary state,
// and a non-mutating backup prerequisite without emitting database contents.

(() => {
    const authenticationSucceeded = (result) => result === 1 || result?.ok === 1;
    const requiredEnvironment = [
        "MONGODB_ADMIN_USER",
        "MONGODB_ADMIN_PASSWORD",
        "MONGODB_BACKUP_USER",
        "MONGODB_BACKUP_PASSWORD",
        "MONGODB_REPLICA_SET",
    ];

    for (const name of requiredEnvironment) {
        if (!process.env[name]) {
            throw new Error(`Missing required backup verification environment: ${name}`);
        }
    }

    const adminDatabase = db.getSiblingDB("admin");
    if (
        !authenticationSucceeded(
            adminDatabase.auth({
                user: process.env.MONGODB_ADMIN_USER,
                pwd: process.env.MONGODB_ADMIN_PASSWORD,
                mechanism: "SCRAM-SHA-256",
            }),
        )
    ) {
        throw new Error("Administrative authentication failed.");
    }

    const backupUser = process.env.MONGODB_BACKUP_USER;
    const backupIdentity = adminDatabase.runCommand({
        usersInfo: { user: backupUser, db: "admin" },
        showCredentials: false,
    });
    if (
        backupIdentity.ok !== 1 ||
        backupIdentity.users.length !== 1 ||
        JSON.stringify(backupIdentity.users[0].roles) !==
            JSON.stringify([{ role: "backup", db: "admin" }]) ||
        JSON.stringify(backupIdentity.users[0].mechanisms) !==
            JSON.stringify(["SCRAM-SHA-256"])
    ) {
        throw new Error("The backup identity must use only SCRAM-SHA-256 and exactly backup@admin.");
    }

    adminDatabase.logout();
    if (
        !authenticationSucceeded(
            adminDatabase.auth({
                user: backupUser,
                pwd: process.env.MONGODB_BACKUP_PASSWORD,
                mechanism: "SCRAM-SHA-256",
            }),
        )
    ) {
        throw new Error("Backup authentication failed.");
    }

    const authenticatedRoles = adminDatabase
        .runCommand({ connectionStatus: 1 })
        .authInfo.authenticatedUserRoles.map(
            ({ role, db: roleDatabase }) => `${role}@${roleDatabase}`,
        )
        .sort();
    if (JSON.stringify(authenticatedRoles) !== JSON.stringify(["backup@admin"])) {
        throw new Error("The authenticated backup session does not have exactly backup@admin.");
    }

    const listDatabases = adminDatabase.runCommand({
        listDatabases: 1,
        nameOnly: true,
        authorizedDatabases: true,
    });
    const hello = adminDatabase.runCommand({ hello: 1 });
    if (
        listDatabases.ok !== 1 ||
        hello.ok !== 1 ||
        hello.setName !== process.env.MONGODB_REPLICA_SET ||
        hello.isWritablePrimary !== true
    ) {
        throw new Error("The backup identity cannot inspect the expected writable replica set.");
    }
})();
