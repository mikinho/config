/* global db, rs, sleep */

// Changes only the sole replica-set member's advertised host after TLS and the
// private listener are active. It refuses multi-member or structurally
// unexpected configurations.

(() => {
    const adminUser = process.env.MONGODB_ADMIN_USER;
    const adminPassword = process.env.MONGODB_ADMIN_PASSWORD;
    const memberHost = process.env.MONGODB_MEMBER_HOST;
    if (!adminUser || !adminPassword || !memberHost) {
        throw new Error("Missing required replica-set reconfiguration environment.");
    }

    const adminDatabase = db.getSiblingDB("admin");
    if (adminDatabase.auth({ user: adminUser, pwd: adminPassword, mechanism: "SCRAM-SHA-256" }) !== 1) {
        throw new Error("Administrative authentication failed.");
    }

    const configuration = rs.conf();
    if (!Array.isArray(configuration.members) || configuration.members.length !== 1 || configuration.members[0]._id !== 0) {
        throw new Error("Network transition requires exactly one replica-set member with _id 0.");
    }
    if (configuration.members[0].host === memberHost) {
        return;
    }
    configuration.members[0].host = memberHost;
    configuration.version += 1;
    rs.reconfig(configuration);

    for (let attempt = 0; attempt < 120; attempt += 1) {
        const hello = adminDatabase.runCommand({ hello: 1 });
        if (hello.ok === 1 && hello.setName === configuration._id && hello.isWritablePrimary === true) {
            return;
        }
        sleep(500);
    }
    throw new Error("The reconfigured replica-set member did not become primary within 60 seconds.");
})();
