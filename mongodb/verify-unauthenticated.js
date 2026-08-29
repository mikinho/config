/* global db */

// A successful privileged command without credentials is a verification
// failure. Connection errors also fail rather than being mistaken for denial.

(() => {
    const isUnauthorized = (result) => result?.code === 13 || result?.codeName === "Unauthorized";
    let denied = false;
    try {
        const result = db.getSiblingDB("admin").runCommand({ usersInfo: 1 });
        if (result.ok === 1) {
            throw new Error("Unauthenticated usersInfo unexpectedly succeeded.");
        }
        denied = result.ok === 0 && isUnauthorized(result);
        if (!denied) {
            throw new Error(
                `Unauthenticated command failed for an unexpected reason: ${result.codeName || result.code}`,
            );
        }
    } catch (error) {
        if (error?.message === "Unauthenticated usersInfo unexpectedly succeeded.") {
            throw error;
        }
        denied = isUnauthorized(error);
    }
    if (!denied) {
        throw new Error("Unauthenticated command did not fail with an authorization denial.");
    }
})();
