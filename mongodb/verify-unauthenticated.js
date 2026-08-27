/* global db */

// A successful privileged command without credentials is a verification
// failure. Connection errors also fail rather than being mistaken for denial.

(() => {
    const result = db.getSiblingDB("admin").runCommand({ usersInfo: 1 });
    if (result.ok === 1) {
        throw new Error("Unauthenticated usersInfo unexpectedly succeeded.");
    }
    if (result.code !== 13) {
        throw new Error(`Unauthenticated command failed for an unexpected reason: ${result.codeName || result.code}`);
    }
})();
