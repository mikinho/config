/* global db */

// Creates one least-privilege application identity after authenticating with
// the user-administration identity. Secrets are supplied through the root
// process environment and are never printed.

(() => {
    const requiredEnvironment = [
        "MONGODB_ADMIN_USER",
        "MONGODB_ADMIN_PASSWORD",
        "MONGODB_APPLICATION_USER",
        "MONGODB_APPLICATION_PASSWORD",
        "MONGODB_APPLICATION_DATABASE",
    ];

    for (const name of requiredEnvironment) {
        if (!process.env[name]) {
            throw new Error(`Missing required user-creation environment: ${name}`);
        }
    }

    const adminDatabase = db.getSiblingDB("admin");
    if (
        adminDatabase.auth({
            user: process.env.MONGODB_ADMIN_USER,
            pwd: process.env.MONGODB_ADMIN_PASSWORD,
            mechanism: "SCRAM-SHA-256",
        }) !== 1
    ) {
        throw new Error("Administrative authentication failed.");
    }

    const applicationDatabase = process.env.MONGODB_APPLICATION_DATABASE;
    const applicationUser = process.env.MONGODB_APPLICATION_USER;
    const targetDatabase = adminDatabase.getSiblingDB(applicationDatabase);
    const existingUser = targetDatabase.runCommand({ usersInfo: { user: applicationUser, db: applicationDatabase } });
    if (existingUser.ok !== 1) {
        throw new Error("Could not inspect the requested application user.");
    }
    if (existingUser.users.length !== 0) {
        throw new Error("The requested application user already exists; rotate or alter it through a reviewed change.");
    }

    targetDatabase.createUser({
        user: applicationUser,
        pwd: process.env.MONGODB_APPLICATION_PASSWORD,
        mechanisms: ["SCRAM-SHA-256"],
        roles: [{ role: "readWrite", db: applicationDatabase }],
    });
})();
