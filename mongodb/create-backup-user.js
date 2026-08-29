/* global db */

// Creates one least-privilege backup identity after authenticating with the
// user-administration identity. Secrets arrive only through the root process
// environment and are never printed.

(() => {
    const authenticationSucceeded = (result) => result === 1 || result?.ok === 1;
    const requiredEnvironment = [
        "MONGODB_ADMIN_USER",
        "MONGODB_ADMIN_PASSWORD",
        "MONGODB_BACKUP_USER",
        "MONGODB_BACKUP_PASSWORD",
    ];

    for (const name of requiredEnvironment) {
        if (!process.env[name]) {
            throw new Error(`Missing required backup-user environment: ${name}`);
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
    const existingUser = adminDatabase.runCommand({
        usersInfo: { user: backupUser, db: "admin" },
    });
    if (existingUser.ok !== 1) {
        throw new Error("Could not inspect the requested backup user.");
    }
    if (existingUser.users.length !== 0) {
        throw new Error("The requested backup user already exists; rotate it through a reviewed change.");
    }

    adminDatabase.createUser({
        user: backupUser,
        pwd: process.env.MONGODB_BACKUP_PASSWORD,
        mechanisms: ["SCRAM-SHA-256"],
        roles: [{ role: "backup", db: "admin" }],
    });
})();
