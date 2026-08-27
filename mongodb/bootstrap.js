/* global db, rs, sleep */

// Executed only by mongodb/setup during first initialization. Secrets arrive
// through the root process environment and are never printed or placed in an
// argument. This file intentionally contains no deployment-specific values.

(() => {
    const requiredEnvironment = [
        "MONGODB_ADMIN_USER",
        "MONGODB_ADMIN_PASSWORD",
        "MONGODB_APPLICATION_USER",
        "MONGODB_APPLICATION_PASSWORD",
        "MONGODB_APPLICATION_DATABASE",
        "MONGODB_REPLICA_SET",
    ];

    for (const name of requiredEnvironment) {
        if (!process.env[name]) {
            throw new Error(`Missing required bootstrap environment: ${name}`);
        }
    }

    const replicaSet = process.env.MONGODB_REPLICA_SET;
    const adminUser = process.env.MONGODB_ADMIN_USER;
    const adminPassword = process.env.MONGODB_ADMIN_PASSWORD;
    const applicationUser = process.env.MONGODB_APPLICATION_USER;
    const applicationPassword = process.env.MONGODB_APPLICATION_PASSWORD;
    const applicationDatabase = process.env.MONGODB_APPLICATION_DATABASE;

    rs.initiate({
        _id: replicaSet,
        members: [{ _id: 0, host: "127.0.0.1:27017" }],
    });

    let primary = false;
    for (let attempt = 0; attempt < 120; attempt += 1) {
        const hello = db.getSiblingDB("admin").runCommand({ hello: 1 });
        if (hello.ok === 1 && hello.isWritablePrimary === true) {
            primary = true;
            break;
        }
        sleep(500);
    }
    if (!primary) {
        throw new Error("The new replica set did not elect a primary within 60 seconds.");
    }

    const adminDatabase = db.getSiblingDB("admin");
    adminDatabase.createUser({
        user: adminUser,
        pwd: adminPassword,
        mechanisms: ["SCRAM-SHA-256"],
        roles: [
            { role: "clusterAdmin", db: "admin" },
            { role: "userAdminAnyDatabase", db: "admin" },
        ],
    });
    if (adminDatabase.auth({ user: adminUser, pwd: adminPassword, mechanism: "SCRAM-SHA-256" }) !== 1) {
        throw new Error("The new administrative user could not authenticate.");
    }

    adminDatabase.getSiblingDB(applicationDatabase).createUser({
        user: applicationUser,
        pwd: applicationPassword,
        mechanisms: ["SCRAM-SHA-256"],
        roles: [{ role: "readWrite", db: applicationDatabase }],
    });
})();
