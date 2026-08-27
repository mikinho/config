/* global db, rs */

// Authenticated functional checks used by mongodb/verify. This script emits no
// credentials or database contents.

(() => {
    const requiredEnvironment = [
        "MONGODB_ADMIN_USER",
        "MONGODB_ADMIN_PASSWORD",
        "MONGODB_APPLICATION_USER",
        "MONGODB_APPLICATION_DATABASE",
        "MONGODB_REPLICA_SET",
        "MONGODB_MODEL",
    ];
    for (const name of requiredEnvironment) {
        if (!process.env[name]) {
            throw new Error(`Missing required verification environment: ${name}`);
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

    const authenticatedRoles = adminDatabase.runCommand({ connectionStatus: 1 }).authInfo.authenticatedUserRoles;
    const normalizedRoles = authenticatedRoles.map(({ role, db: roleDatabase }) => `${role}@${roleDatabase}`).sort();
    const expectedAdminRoles = ["clusterAdmin@admin", "userAdminAnyDatabase@admin"];
    if (JSON.stringify(normalizedRoles) !== JSON.stringify(expectedAdminRoles)) {
        throw new Error("The administrative user does not have exactly clusterAdmin and userAdminAnyDatabase.");
    }
    const administrativeUsers = adminDatabase.runCommand({
        usersInfo: { user: process.env.MONGODB_ADMIN_USER, db: "admin" },
        showCredentials: false,
    });
    if (
        administrativeUsers.ok !== 1 ||
        administrativeUsers.users.length !== 1 ||
        JSON.stringify(administrativeUsers.users[0].mechanisms) !== JSON.stringify(["SCRAM-SHA-256"])
    ) {
        throw new Error("The administrative user must use only SCRAM-SHA-256.");
    }

    const hello = adminDatabase.runCommand({ hello: 1 });
    if (hello.ok !== 1 || hello.setName !== process.env.MONGODB_REPLICA_SET || hello.isWritablePrimary !== true) {
        throw new Error("The expected replica set is not a writable primary.");
    }

    const expectedMemberHost =
        process.env.MONGODB_MODEL === "local"
            ? "127.0.0.1:27017"
            : `${process.env.MONGODB_MEMBER_HOST}:27017`;
    const replicaConfiguration = rs.conf();
    if (
        !Array.isArray(replicaConfiguration.members) ||
        replicaConfiguration.members.length !== 1 ||
        replicaConfiguration.members[0]._id !== 0 ||
        replicaConfiguration.members[0].host !== expectedMemberHost
    ) {
        throw new Error("The replica set does not contain exactly the expected single member.");
    }

    const commandLine = adminDatabase.runCommand({ getCmdLineOpts: 1 });
    if (commandLine.ok !== 1) {
        throw new Error("Could not inspect the effective MongoDB configuration.");
    }
    const parsed = commandLine.parsed || {};
    if (
        parsed.security?.authorization !== "enabled" ||
        parsed.security?.keyFile !== "/etc/mongod.keyfile" ||
        parsed.security?.javascriptEnabled !== false ||
        parsed.replication?.replSetName !== process.env.MONGODB_REPLICA_SET
    ) {
        throw new Error("The effective authorization, key-file, scripting, or replica-set configuration is unsafe.");
    }
    const expectedBindIp =
        process.env.MONGODB_MODEL === "local"
            ? "127.0.0.1"
            : `127.0.0.1,${process.env.MONGODB_BIND_ADDRESS}`;
    if (parsed.net?.bindIp !== expectedBindIp || parsed.net?.port !== 27017) {
        throw new Error("The effective MongoDB listener does not match the selected model.");
    }
    if (process.env.MONGODB_MODEL === "local") {
        if (parsed.net?.tls?.mode) {
            throw new Error("The local model has an unexpected TLS network configuration.");
        }
    } else if (
        parsed.net?.tls?.mode !== "requireTLS" ||
        parsed.net?.tls?.certificateKeyFile !== process.env.MONGODB_TLS_CERTIFICATE_KEY_FILE ||
        parsed.net?.tls?.CAFile !== process.env.MONGODB_TLS_CA_FILE ||
        parsed.net?.tls?.allowConnectionsWithoutCertificates !== true ||
        parsed.net?.tls?.allowInvalidCertificates !== false ||
        parsed.net?.tls?.allowInvalidHostnames !== false
    ) {
        throw new Error("The effective network model does not require the expected TLS identity and CA.");
    }

    const parameter = adminDatabase.runCommand({ getParameter: 1, enableLocalhostAuthBypass: 1 });
    if (parameter.ok !== 1 || parameter.enableLocalhostAuthBypass !== false) {
        throw new Error("enableLocalhostAuthBypass is not disabled.");
    }

    const applicationDatabase = process.env.MONGODB_APPLICATION_DATABASE;
    const applicationUser = process.env.MONGODB_APPLICATION_USER;
    const users = adminDatabase
        .getSiblingDB(applicationDatabase)
        .runCommand({ usersInfo: { user: applicationUser, db: applicationDatabase }, showCredentials: false });
    if (users.ok !== 1 || users.users.length !== 1) {
        throw new Error("The expected application user is missing.");
    }
    const roles = users.users[0].roles;
    if (
        !Array.isArray(roles) ||
        roles.length !== 1 ||
        roles[0].role !== "readWrite" ||
        roles[0].db !== applicationDatabase
    ) {
        throw new Error("The application user does not have exactly readWrite on its own database.");
    }
    if (JSON.stringify(users.users[0].mechanisms) !== JSON.stringify(["SCRAM-SHA-256"])) {
        throw new Error("The application user must use only SCRAM-SHA-256.");
    }
})();
