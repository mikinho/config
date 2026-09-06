/** Behavioral checks for the isolated nginx upstream fixture. */

import assert from "node:assert/strict";
import { once } from "node:events";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { request } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { startEchoServer } from "./fixtures/nginx-node-echo.mjs";

/**
 * Read one response from the fixture over its Unix socket.
 *
 * @param {string} socketPath Listening Unix socket.
 * @param {string} path Request target, including any query.
 * @returns {Promise<{headers: import("node:http").IncomingHttpHeaders, body: object}>}
 */
async function fetchSocket(socketPath, path) {
    return new Promise((resolve, reject) => {
        const outgoing = request({ socketPath, path, headers: { Host: "fixture.invalid" } }, (incoming) => {
            let body = "";
            incoming.setEncoding("utf8");
            incoming.on("data", (chunk) => { body += chunk; });
            incoming.on("error", reject);
            incoming.on("end", () => {
                try {
                    resolve({ headers: incoming.headers, body: JSON.parse(body) });
                } catch (error) {
                    reject(error);
                }
            });
        });
        outgoing.on("error", reject);
        outgoing.end();
    });
}

test("echo preserves the request and provides application policy only on its route", async () => {
    const directory = await mkdtemp(join(tmpdir(), "nginx-echo-"));
    const socketPath = join(directory, "app.sock");
    let server;
    try {
        server = await startEchoServer(socketPath);
        const normal = await fetchSocket(socketPath, "/static/missing.css?kept=yes");
        assert.equal(normal.body.method, "GET");
        assert.equal(normal.body.url, "/static/missing.css?kept=yes");
        assert.equal(normal.body.headers.host, "fixture.invalid");
        assert.equal(normal.headers["cache-control"], "private, no-store");
        assert.equal(normal.headers["content-security-policy"], undefined);
        const policy = await fetchSocket(socketPath, "/application-policy");
        assert.equal(policy.headers["content-security-policy"], "default-src 'none'");
    } finally {
        if (server) {
            const closed = once(server, "close");
            server.close();
            server.closeIdleConnections();
            await closed;
        }
        await rm(directory, { recursive: true, force: true });
    }
});

test("fixture rejects relative paths and preserves an occupied filesystem path", async () => {
    await assert.rejects(startEchoServer("relative.sock"), /socket path must be absolute/);
    const directory = await mkdtemp(join(tmpdir(), "nginx-echo-"));
    const occupiedPath = join(directory, "occupied.sock");
    try {
        await writeFile(occupiedPath, "preserve this fixture");
        await assert.rejects(startEchoServer(occupiedPath), { code: "EADDRINUSE" });
        assert.equal(await readFile(occupiedPath, "utf8"), "preserve this fixture");
    } finally {
        await rm(directory, { recursive: true, force: true });
    }
});
