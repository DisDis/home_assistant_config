// Run inside the Uptime Kuma container; see ../README.md.
const { io } = require("socket.io-client");

const username = process.env.UPTIME_KUMA_USERNAME;
const password = process.env.UPTIME_KUMA_PASSWORD;
if (!username || !password) {
    throw new Error("Set UPTIME_KUMA_USERNAME and UPTIME_KUMA_PASSWORD");
}

const socket = io("http://127.0.0.1:3001", {
    transports: ["websocket"],
    autoConnect: false,
    reconnection: false,
});
let needsSetup = false;
let existingMonitors = {};
socket.on("setup", () => { needsSetup = true; });
socket.on("monitorList", (monitors) => { existingMonitors = monitors; });

function call(event, ...args) {
    return new Promise((resolve, reject) => {
        socket.timeout(15000).emit(event, ...args, (error, result) => {
            if (error) {
                reject(new Error(`${event}: request timed out`));
            } else {
                resolve(result);
            }
        });
    });
}

const address = process.env.MONITOR_HOST;
if (!address || !/^[a-zA-Z0-9.-]+$/.test(address)) {
    throw new Error("Set MONITOR_HOST to the server IPv4 address or DNS name");
}
const monitors = [
    { name: "Home Assistant", type: "http", url: `http://${address}:8123/` },
    { name: "Mosquitto MQTT", type: "port", hostname: address, port: 1883 },
    { name: "Zigbee2MQTT", type: "http", url: `http://${address}:8080/` },
    { name: "Matter Server", type: "port", hostname: address, port: 5580 },
    // Cloud Commander is disabled; restore this check only with that service.
    // { name: "Cloud Commander", type: "http", url: `http://${address}:8001/`, accepted_statuscodes: ["401"] },
    { name: "HASS Configurator", type: "http", url: `http://${address}:3218/`, accepted_statuscodes: ["401"] },
    { name: "Portainer", type: "http", url: `https://${address}:9443/api/status`, ignoreTls: true },
    { name: "Cockpit", type: "http", url: `https://${address}:9090/`, ignoreTls: true },
];

async function main() {
    await new Promise((resolve, reject) => {
        // The server registers login handlers after sending its initial info.
        socket.once("info", resolve);
        socket.once("connect_error", () => reject(new Error("Cannot connect to Uptime Kuma")));
        socket.connect();
    });

    let login = await call("login", { username, password });
    if (!login.ok && needsSetup) {
        const setup = await call("setup", username, password);
        if (!setup.ok) {
            throw new Error("Could not create the initial administrator");
        }
        login = await call("login", { username, password });
    }
    if (!login.ok) {
        throw new Error("Login failed; existing credentials are not changed");
    }

    let created = 0;
    for (const monitor of monitors) {
        if (Object.values(existingMonitors).some((existing) => existing.name === monitor.name)) {
            continue;
        }
        const result = await call("add", {
            interval: 60,
            retryInterval: 60,
            resendInterval: 0,
            maxretries: 2,
            timeout: 20,
            method: "GET",
            maxredirects: 10,
            accepted_statuscodes: ["200-299"],
            notificationIDList: {},
            ignoreTls: false,
            upsideDown: false,
            conditions: [],
            ...monitor,
        });
        if (!result.ok) {
            throw new Error(`Could not add monitor: ${monitor.name}`);
        }
        created += 1;
    }
    console.log(`Monitoring configured: ${monitors.length} checks, ${created} created`);
}

const deadline = setTimeout(() => {
    console.error("Uptime Kuma setup timed out");
    socket.disconnect();
    process.exit(1);
}, 60000);

main().catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
}).finally(() => {
    clearTimeout(deadline);
    socket.disconnect();
});
