/* global db */

// Authenticates as one application identity, proves a read on its own
// authentication database, and proves denial on the administrative database.
// It does not write data or emit credentials or database contents.

(() => {
    const requiredEnvironment = [
        "MONGODB_APPLICATION_USER",
        "MONGODB_APPLICATION_PASSWORD",
        "MONGODB_APPLICATION_DATABASE",
    ];
    for (const name of requiredEnvironment) {
        if (!process.env[name]) {
            throw new Error(`Missing required application verification environment: ${name}`);
        }
    }

    const applicationDatabaseName = process.env.MONGODB_APPLICATION_DATABASE;
    const applicationDatabase = db.getSiblingDB(applicationDatabaseName);
    if (
        applicationDatabase.auth({
            user: process.env.MONGODB_APPLICATION_USER,
            pwd: process.env.MONGODB_APPLICATION_PASSWORD,
            mechanism: "SCRAM-SHA-256",
        }) !== 1
    ) {
        throw new Error("Application authentication failed.");
    }

    const connection = applicationDatabase.runCommand({ connectionStatus: 1 });
    const normalizedRoles = connection.authInfo.authenticatedUserRoles
        .map(({ role, db: roleDatabase }) => `${role}@${roleDatabase}`)
        .sort();
    if (JSON.stringify(normalizedRoles) !== JSON.stringify([`readWrite@${applicationDatabaseName}`])) {
        throw new Error("The authenticated application session does not have exactly its expected role.");
    }

    const ownDatabaseRead = applicationDatabase.runCommand({
        find: "__config_mongodb_verification__",
        filter: {},
        limit: 1,
    });
    if (ownDatabaseRead.ok !== 1) {
        throw new Error("The application identity cannot read its own database.");
    }

    const administrativeRead = applicationDatabase.getSiblingDB("admin").runCommand({
        find: "system.users",
        filter: {},
        limit: 1,
    });
    if (administrativeRead.ok !== 0 || administrativeRead.code !== 13) {
        throw new Error("The application identity was not denied access to the administrative database.");
    }
})();
