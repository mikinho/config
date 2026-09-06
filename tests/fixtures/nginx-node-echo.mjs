/**
 * Isolated HTTP fixture for exercising the actual nginx Node.js site example.
 * It listens only on the supplied Unix socket and never opens an IP listener.
 */

import { createServer } from "node:http";
import { isAbsolute } from "node:path";
import { pathToFileURL } from "node:url";

const APPLICATION_POLICY_PATH = "/application-policy";
const APPLICATION_CSP = "default-src 'none'";
const APPLICATION_CACHE_CONTROL = "private, no-store";
const HTTP_TIMEOUT_MS = 10_000;

/**
 * Echo the upstream request, with one route providing an application-owned CSP.
 *
 * @param {import("node:http").IncomingMessage} request Upstream HTTP request.
 * @param {import("node:http").ServerResponse} response Upstream HTTP response.
 */
function respond(request, response) {
    request.resume();
    response.setHeader("Content-Type", "application/json");
    response.setHeader("Cache-Control", APPLICATION_CACHE_CONTROL);
    if (request.url === APPLICATION_POLICY_PATH) {
        response.setHeader("Content-Security-Policy", APPLICATION_CSP);
    }
    response.end(JSON.stringify({
        method: request.method,
        url: request.url,
        headers: request.headers,
    }));
}

/**
 * Start a fixture without replacing or deleting an existing socket path.
 * The caller owns filesystem permissions and shutdown.
 *
 * @param {string} socketPath Absolute path for a new Unix socket.
 * @returns {Promise<import("node:http").Server>} Listening HTTP server.
 */
export async function startEchoServer(socketPath) {
    if (!isAbsolute(socketPath)) {
        throw new Error("socket path must be absolute");
    }
    const server = createServer(respond);
    server.requestTimeout = HTTP_TIMEOUT_MS;
    server.headersTimeout = HTTP_TIMEOUT_MS;
    await new Promise((resolve, reject) => {
        server.once("error", reject);
        server.listen(socketPath, () => {
            server.removeListener("error", reject);
            resolve();
        });
    });
    return server;
}

/**
 * Run the fixture until CI requests shutdown; close keepalive connections too.
 *
 * @returns {Promise<void>}
 */
async function main() {
    if (process.argv.length !== 3) {
        throw new Error("usage: node nginx-node-echo.mjs /absolute/socket/path");
    }
    const server = await startEchoServer(process.argv[2]);
    const stop = () => {
        server.close();
        server.closeIdleConnections();
    };
    process.once("SIGINT", stop);
    process.once("SIGTERM", stop);
    server.on("error", (error) => {
        process.stderr.write(`nginx-node-echo: ${error.message}\n`);
        process.exitCode = 1;
        server.close();
    });
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
    main().catch((error) => {
        process.stderr.write(`nginx-node-echo: ${error.message}\n`);
        process.exitCode = 1;
    });
}
