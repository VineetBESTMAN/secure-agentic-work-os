import { readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";
import { spawn } from "node:child_process";

function requiredSecret(path, label) {
  let value = "";
  try {
    value = readFileSync(path, "utf8").trim();
  } catch {
    throw new Error(`${label} secret could not be read.`);
  }
  if (!value) throw new Error(`${label} secret is empty.`);
  return value;
}

const workOsToken = requiredSecret(
  process.env.WORKOS_MCP_TOKEN_FILE || "/run/secrets/openclaw_workos_token",
  "Work OS MCP token",
);
const gatewayToken = requiredSecret(
  process.env.OPENCLAW_GATEWAY_TOKEN_FILE || "/run/secrets/openclaw_gateway_token",
  "OpenClaw gateway token",
);
const configPath = process.env.OPENCLAW_CONFIG_PATH || "/run/openclaw/openclaw.json";
const mcpUrl = process.env.WORKOS_MCP_URL || "http://backend:8000/protocol/mcp";
const allowedTools = (process.env.WORKOS_MCP_TOOLS || "search_documents,create_task")
  .split(",")
  .map((value) => value.trim())
  .filter(Boolean);

const config = {
  gateway: {
    mode: "local",
    bind: "lan",
    auth: { mode: "token", token: gatewayToken },
  },
  tools: {
    profile: "messaging",
    deny: [
      "exec",
      "process",
      "read",
      "write",
      "edit",
      "apply_patch",
      "browser",
      "web_fetch",
      "web_search",
      "sessions_spawn",
    ],
  },
  mcp: {
    servers: {
      "secure-work-os": {
        url: mcpUrl,
        transport: "streamable-http",
        headers: { Authorization: `Bearer ${workOsToken}` },
        connectTimeout: 10,
        timeout: 30,
        supportsParallelToolCalls: false,
        toolFilter: { include: allowedTools },
      },
    },
  },
};

mkdirSync(dirname(configPath), { recursive: true, mode: 0o700 });
writeFileSync(configPath, `${JSON.stringify(config, null, 2)}\n`, { mode: 0o600 });

const args = process.argv.slice(2);
if (args.length === 0) throw new Error("An OpenClaw command is required.");
const child = spawn("node", ["dist/index.js", ...args], {
  stdio: "inherit",
  env: process.env,
});

for (const signal of ["SIGINT", "SIGTERM", "SIGHUP"]) {
  process.on(signal, () => child.kill(signal));
}
child.on("exit", (code, signal) => {
  if (signal) process.kill(process.pid, signal);
  else process.exit(code ?? 1);
});
