/**
 * Behavioral regression checks for MongoDB Shell compatibility.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const TEST_DIRECTORY = dirname(fileURLToPath(import.meta.url));
const MONGODB_DIRECTORY = join(TEST_DIRECTORY, "..", "mongodb");

/**
 * Execute one mongosh script in an isolated mocked shell context.
 *
 * @param {string} filename MongoDB component filename.
 * @param {Record<string, unknown>} context Script-specific globals.
 */
function executeScript(filename, context) {
    const source = readFileSync(join(MONGODB_DIRECTORY, filename), "utf8");
    vm.runInNewContext(
        source,
        {
            Array,
            EJSON: { stringify: JSON.stringify },
            Error,
            JSON,
            Number,
            print: () => {},
            ...context,
        },
        { filename },
    );
}

function testBootstrapObjectAuthentication() {
    let applicationUserCreated = false;
    const applicationDatabase = {
        createUser: () => {
            applicationUserCreated = true;
        },
    };
    const adminDatabase = {
        auth: () => ({ ok: 1 }),
        createUser: () => {},
        getSiblingDB: () => applicationDatabase,
        runCommand: () => ({ isWritablePrimary: true, ok: 1 }),
    };

    executeScript("bootstrap.js", {
        db: { getSiblingDB: () => adminDatabase },
        process: {
            env: {
                MONGODB_ADMIN_PASSWORD: "admin-secret",
                MONGODB_ADMIN_USER: "database_admin",
                MONGODB_APPLICATION_DATABASE: "application",
                MONGODB_APPLICATION_PASSWORD: "application-secret",
                MONGODB_APPLICATION_USER: "application_user",
                MONGODB_REPLICA_SET: "example-rs",
            },
        },
        rs: { initiate: () => {} },
        sleep: () => {},
    });

    assert.equal(applicationUserCreated, true);
}

function testAddApplicationUserObjectAuthentication() {
    let applicationUserCreated = false;
    const applicationDatabase = {
        createUser: () => {
            applicationUserCreated = true;
        },
        runCommand: () => ({ ok: 1, users: [] }),
    };
    const adminDatabase = {
        auth: () => ({ ok: 1 }),
        getSiblingDB: () => applicationDatabase,
    };

    executeScript("add-application-user.js", {
        db: { getSiblingDB: () => adminDatabase },
        process: {
            env: {
                MONGODB_ADMIN_PASSWORD: "admin-secret",
                MONGODB_ADMIN_USER: "database_admin",
                MONGODB_APPLICATION_DATABASE: "application",
                MONGODB_APPLICATION_PASSWORD: "application-secret",
                MONGODB_APPLICATION_USER: "application_user",
            },
        },
    });

    assert.equal(applicationUserCreated, true);
}

function testBackupUserObjectAuthenticationAndVerification() {
    let backupUserCreated = false;
    const creationAdminDatabase = {
        auth: () => ({ ok: 1 }),
        createUser: () => {
            backupUserCreated = true;
        },
        runCommand: () => ({ ok: 1, users: [] }),
    };
    const environment = {
        MONGODB_ADMIN_PASSWORD: "admin-secret",
        MONGODB_ADMIN_USER: "database_admin",
        MONGODB_BACKUP_PASSWORD: "backup-secret",
        MONGODB_BACKUP_USER: "backup_user",
        MONGODB_REPLICA_SET: "example-rs",
    };

    executeScript("create-backup-user.js", {
        db: { getSiblingDB: () => creationAdminDatabase },
        process: { env: environment },
    });
    assert.equal(backupUserCreated, true);

    let authenticationCount = 0;
    const verificationAdminDatabase = {
        auth: () => {
            authenticationCount += 1;
            return { ok: 1 };
        },
        logout: () => {},
        runCommand: (command) => {
            if (command.usersInfo) {
                return {
                    ok: 1,
                    users: [
                        {
                            mechanisms: ["SCRAM-SHA-256"],
                            roles: [{ role: "backup", db: "admin" }],
                        },
                    ],
                };
            }
            if (command.connectionStatus) {
                return {
                    authInfo: {
                        authenticatedUserRoles: [
                            { role: "backup", db: "admin" },
                        ],
                    },
                };
            }
            if (command.listDatabases) {
                return { ok: 1 };
            }
            if (command.hello) {
                return {
                    isWritablePrimary: true,
                    ok: 1,
                    setName: "example-rs",
                };
            }
            throw new Error("Unexpected mocked backup verification command.");
        },
    };

    executeScript("verify-backup.js", {
        db: { getSiblingDB: () => verificationAdminDatabase },
        process: { env: environment },
    });
    assert.equal(authenticationCount, 2);
}

function testReconfigureObjectAuthentication() {
    executeScript("reconfigure-member.js", {
        db: {
            getSiblingDB: () => ({
                auth: () => ({ ok: 1 }),
            }),
        },
        process: {
            env: {
                MONGODB_ADMIN_PASSWORD: "admin-secret",
                MONGODB_ADMIN_USER: "database_admin",
                MONGODB_MEMBER_HOST: "mongodb.internal.example:27017",
            },
        },
        rs: {
            conf: () => ({
                _id: "example-rs",
                members: [
                    {
                        _id: 0,
                        host: "mongodb.internal.example:27017",
                    },
                ],
                version: 1,
            }),
        },
        sleep: () => {},
    });
}

function testAuthenticatedVerifierObjectAuthentication() {
    const applicationDatabase = {
        runCommand: () => ({
            ok: 1,
            users: [
                {
                    mechanisms: ["SCRAM-SHA-256"],
                    roles: [{ db: "application", role: "readWrite" }],
                },
            ],
        }),
    };
    const adminDatabase = {
        auth: () => ({ ok: 1 }),
        getSiblingDB: () => applicationDatabase,
        runCommand: (command) => {
            if (command.connectionStatus) {
                return {
                    authInfo: {
                        authenticatedUserRoles: [
                            { db: "admin", role: "userAdminAnyDatabase" },
                            { db: "admin", role: "clusterAdmin" },
                        ],
                    },
                };
            }
            if (command.usersInfo) {
                return {
                    ok: 1,
                    users: [{ mechanisms: ["SCRAM-SHA-256"] }],
                };
            }
            if (command.hello) {
                return {
                    isWritablePrimary: true,
                    ok: 1,
                    setName: "example-rs",
                };
            }
            if (command.buildInfo) {
                return { ok: 1, version: "8.0.29" };
            }
            if (command.featureCompatibilityVersion) {
                return {
                    featureCompatibilityVersion: { version: "8.0" },
                    ok: 1,
                };
            }
            if (command.serverStatus) {
                return {
                    ok: 1,
                    tcmalloc: {
                        tcmalloc: { cpu_free: 1 },
                        usingPerCPUCaches: true,
                    },
                };
            }
            if (command.getCmdLineOpts) {
                return {
                    ok: 1,
                    parsed: {
                        net: { bindIp: "127.0.0.1", port: 27017 },
                        replication: { replSetName: "example-rs" },
                        security: {
                            authorization: "enabled",
                            javascriptEnabled: false,
                            keyFile: "/etc/mongod.keyfile",
                        },
                    },
                };
            }
            if (command.enableLocalhostAuthBypass) {
                return { enableLocalhostAuthBypass: false, ok: 1 };
            }
            throw new Error("Unexpected mocked administrative command.");
        },
    };

    executeScript("verify-auth.js", {
        db: { getSiblingDB: () => adminDatabase },
        process: {
            env: {
                MONGODB_ADMIN_PASSWORD: "admin-secret",
                MONGODB_ADMIN_USER: "database_admin",
                MONGODB_APPLICATION_DATABASE: "application",
                MONGODB_APPLICATION_USER: "application_user",
                MONGODB_MODEL: "local",
                MONGODB_REPLICA_SET: "example-rs",
            },
        },
        rs: {
            conf: () => ({
                members: [{ _id: 0, host: "127.0.0.1:27017" }],
            }),
        },
    });
}

function testThrownApplicationAuthorizationDenial() {
    const administrativeDatabase = {
        runCommand: () => {
            throw Object.assign(new Error("not authorized"), {
                code: 13,
                codeName: "Unauthorized",
            });
        },
    };
    const applicationDatabase = {
        auth: () => ({ ok: 1 }),
        getSiblingDB: () => administrativeDatabase,
        runCommand: (command) => {
            if (command.connectionStatus) {
                return {
                    authInfo: {
                        authenticatedUserRoles: [
                            { db: "application", role: "readWrite" },
                        ],
                    },
                };
            }
            return { ok: 1 };
        },
    };

    executeScript("verify-application.js", {
        db: { getSiblingDB: () => applicationDatabase },
        process: {
            env: {
                MONGODB_APPLICATION_DATABASE: "application",
                MONGODB_APPLICATION_PASSWORD: "application-secret",
                MONGODB_APPLICATION_USER: "application_user",
            },
        },
    });
}

function testThrownUnauthenticatedAuthorizationDenial() {
    executeScript("verify-unauthenticated.js", {
        db: {
            getSiblingDB: () => ({
                runCommand: () => {
                    throw Object.assign(new Error("not authorized"), {
                        code: 13,
                        codeName: "Unauthorized",
                    });
                },
            }),
        },
    });
}

function testUnexpectedUnauthenticatedFailureIsRejected() {
    assert.throws(
        () =>
            executeScript("verify-unauthenticated.js", {
                db: {
                    getSiblingDB: () => ({
                        runCommand: () => {
                            throw Object.assign(new Error("not primary"), {
                                code: 10107,
                                codeName: "NotWritablePrimary",
                            });
                        },
                    }),
                },
            }),
        /did not fail with an authorization denial/u,
    );
}

testBootstrapObjectAuthentication();
testAddApplicationUserObjectAuthentication();
testBackupUserObjectAuthenticationAndVerification();
testReconfigureObjectAuthentication();
testAuthenticatedVerifierObjectAuthentication();
testThrownApplicationAuthorizationDenial();
testThrownUnauthenticatedAuthorizationDenial();
testUnexpectedUnauthenticatedFailureIsRejected();
process.stdout.write("Validated MongoDB Shell 2.10 authentication and authorization-denial behavior.\n");
