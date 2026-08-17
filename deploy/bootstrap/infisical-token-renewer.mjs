import fs from "node:fs";

const tokenPath = process.env.INFISICAL_TOKEN_FILE ?? "/run/platform-secrets/infisical-token";
const baseUrl = (process.env.INFISICAL_BASE_URL ?? "http://infisical:8080").replace(/\/$/, "");
const intervalSeconds = Number(process.env.INFISICAL_TOKEN_RENEW_INTERVAL_SECONDS ?? "86400");
const retrySeconds = Number(process.env.INFISICAL_TOKEN_RENEW_RETRY_SECONDS ?? "60");
const once = process.argv.includes("--once");

const sleep = (seconds) => new Promise((resolve) => setTimeout(resolve, seconds * 1000));

async function renew() {
  const accessToken = fs.readFileSync(tokenPath, "utf8").trim();
  if (!accessToken) {
    throw new Error("Infisical token file is empty");
  }
  const response = await fetch(`${baseUrl}/api/v1/auth/token/renew`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ accessToken }),
  });
  if (!response.ok) {
    throw new Error(`Infisical token renewal returned HTTP ${response.status}`);
  }
  const payload = await response.json();
  if (typeof payload.accessToken !== "string" || !payload.accessToken) {
    throw new Error("Infisical token renewal returned no access token");
  }
  const temporaryPath = `${tokenPath}.new`;
  fs.writeFileSync(temporaryPath, payload.accessToken, { encoding: "utf8", mode: 0o600 });
  fs.chmodSync(temporaryPath, 0o444);
  fs.renameSync(temporaryPath, tokenPath);
  console.log(`Infisical token renewed; next renewal in ${intervalSeconds} seconds`);
}

do {
  try {
    await renew();
    if (once) break;
    await sleep(intervalSeconds);
  } catch (error) {
    console.error(error instanceof Error ? error.message : "Infisical token renewal failed");
    if (once) process.exit(1);
    await sleep(retrySeconds);
  }
} while (true);
