const { URL } = require("url");
const { randomUUID } = require("crypto");
const { getSettingsMap } = require("./db");

function apiBase(panelUrl) {
  const u = new URL(String(panelUrl || ""));
  return `${u.origin}${u.pathname.replace(/\/$/, "")}`;
}

async function getPanelConnectionConfig() {
  const settings = await getSettingsMap([
    "PANEL_URL",
    "PANEL_API_TOKEN",
    "PANEL_USERNAME",
    "PANEL_PASSWORD",
    "SUB_PORT",
  ]).catch(() => ({}));

  return {
    panelUrl: String(settings.PANEL_URL || process.env.PANEL_URL || "").trim(),
    panelApiToken: String(settings.PANEL_API_TOKEN || process.env.PANEL_API_TOKEN || "").trim(),
    panelUsername: String(settings.PANEL_USERNAME || process.env.PANEL_USERNAME || "").trim(),
    panelPassword: String(settings.PANEL_PASSWORD || process.env.PANEL_PASSWORD || "").trim(),
    subPort: Number(settings.SUB_PORT || process.env.SUB_PORT || 0) || 0,
  };
}

function buildSubLink(panelUrl, subId, subPort = 0) {
  const u = new URL(String(panelUrl || ""));
  const port = Number(subPort || 0) > 0 ? Number(subPort) : (u.port ? Number(u.port) : (u.protocol === "https:" ? 443 : 80));
  return `${u.protocol}//${u.hostname}:${port}/sub/${subId}`;
}

async function loginHeaders(cfg = null) {
  const connection = cfg || await getPanelConnectionConfig();
  const headers = { Accept: "application/json" };
  const token = connection.panelApiToken;
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  return headers;
}

async function requestXui(path, { method = "GET", body } = {}) {
  const cfg = await getPanelConnectionConfig();
  if (!cfg.panelUrl) {
    throw new Error("PANEL_URL is not configured.");
  }
  const base = apiBase(cfg.panelUrl || "");
  const url = `${base}/panel/api${path}`;
  const headers = await loginHeaders(cfg);
  const init = { method, headers };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  const res = await fetch(url, init);
  const text = await res.text();
  let json = null;
  try {
    json = text ? JSON.parse(text) : {};
  } catch {
    json = { success: false, msg: text };
  }
  if (!res.ok || json.success === false) {
    throw new Error(json.msg || `XUI request failed (${res.status})`);
  }
  return json.obj ?? json;
}

function isNotFoundError(error) {
  const message = String(error?.message || "").toLowerCase();
  return message.includes("404") || message.includes("not found") || message.includes("پیدا نشد");
}

async function requestXuiRaw(path, { method = "GET", body, accept = "application/octet-stream" } = {}) {
  const cfg = await getPanelConnectionConfig();
  if (!cfg.panelUrl) {
    throw new Error("PANEL_URL is not configured.");
  }
  const base = apiBase(cfg.panelUrl || "");
  const url = `${base}/panel/api${path}`;
  const headers = await loginHeaders(cfg);
  headers.Accept = accept;
  const init = { method, headers };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  const res = await fetch(url, init);
  const buffer = Buffer.from(await res.arrayBuffer());
  if (!res.ok) {
    let detail = "";
    try {
      detail = JSON.parse(buffer.toString("utf8")).msg || "";
    } catch {
      detail = buffer.toString("utf8");
    }
    throw new Error(detail || `XUI request failed (${res.status})`);
  }
  return buffer;
}

async function getInbounds() {
  return requestXui("/inbounds/list");
}

function normalizeInboundIds(inboundIds) {
  const values = Array.isArray(inboundIds) ? inboundIds : [inboundIds];
  const seen = new Set();
  const ids = [];
  for (const value of values) {
    const id = Number(value);
    if (Number.isFinite(id) && id > 0 && !seen.has(id)) {
      seen.add(id);
      ids.push(id);
    }
  }
  return ids;
}

async function resolveCreateInboundIds(inboundIds) {
  const requested = normalizeInboundIds(inboundIds);
  if (requested.length) return requested;

  const inbounds = await getInbounds();
  const enabled = Array.isArray(inbounds)
    ? inbounds
        .filter((inbound) => inbound && inbound.enable !== false && inbound.enable !== 0)
        .map((inbound) => Number(inbound.id))
        .filter((id) => Number.isFinite(id) && id > 0)
    : [];
  const unique = normalizeInboundIds(enabled);
  if (!unique.length) {
    throw new Error("No active inbounds are available for this plan.");
  }
  return unique;
}

async function getClient(email) {
  return requestXui(`/clients/get/${encodeURIComponent(email)}`);
}

async function getClients() {
  return requestXui("/clients/list");
}

async function getClientLinks(email) {
  return requestXui(`/clients/links/${encodeURIComponent(email)}`);
}

async function getSubLinks(subId) {
  return requestXui(`/clients/subLinks/${encodeURIComponent(subId)}`);
}

async function getNewUUID() {
  try {
    const value = await requestXui("/server/getNewUUID");
    if (typeof value === "string" && value.trim()) {
      return value.trim();
    }
    if (value && typeof value === "object") {
      return String(value.uuid || value.id || value.value || "").trim() || randomUUID();
    }
  } catch {
    // fallback below
  }
  return randomUUID();
}

async function createClient({
  inboundIds,
  email,
  trafficGb = 0,
  expireDays = 30,
  expiryTimeMs = null,
  subId = null,
  limitIp = 0,
  tgId = 0,
}) {
  const sub = subId || randomUUID().replace(/-/g, "").slice(0, 16);
  const totalBytes = trafficGb > 0 ? Math.floor(Number(trafficGb) * 1024 ** 3) : 0;
  const expiryTime = expiryTimeMs != null
    ? Number(expiryTimeMs)
    : (expireDays > 0 ? Math.floor((Date.now() + Number(expireDays) * 86400000)) : 0);
  const targetInboundIds = await resolveCreateInboundIds(inboundIds);

  const payload = {
    client: {
      email,
      totalGB: totalBytes,
      expiryTime,
      tgId,
      limitIp: Number(limitIp || 0),
      enable: true,
      subId: sub,
      reset: 0,
    },
    inboundIds: targetInboundIds,
  };

  try {
    await requestXui("/clients/add", { method: "POST", body: payload });
  } catch (error) {
    if (!isNotFoundError(error)) {
      throw error;
    }
    await requestXui("/clients/bulkCreate", { method: "POST", body: [payload] });
  }
  const created = await getClient(email);
  const createdInboundIds = normalizeInboundIds(created?.inboundIds || created?.inbound_ids || []);
  return {
    ...created,
    inboundIds: createdInboundIds.length ? createdInboundIds : targetInboundIds,
  };
}

async function updateClient(email, {
  trafficGb = 0,
  expireDays = 30,
  expiryTimeMs = null,
  enable = true,
  tgId = 0,
  limitIp = 0,
} = {}) {
  const totalBytes = trafficGb > 0 ? Math.floor(Number(trafficGb) * 1024 ** 3) : 0;
  const expiryTime = expiryTimeMs != null
    ? Number(expiryTimeMs)
    : (expireDays > 0 ? Math.floor((Date.now() + Number(expireDays) * 86400000)) : 0);

  await requestXui(`/clients/update/${encodeURIComponent(email)}`, {
    method: "POST",
    body: {
      email,
      totalGB: totalBytes,
      expiryTime,
      tgId,
      enable: Boolean(enable),
      limitIp: Number(limitIp || 0),
    },
  });
}

async function deleteClient(email) {
  await requestXui(`/clients/del/${encodeURIComponent(email)}`, { method: "POST" });
}

async function findClientByUUID(uuid) {
  const target = String(uuid || "").trim().toLowerCase();
  if (!target) return null;
  const clients = await getClients().catch(() => []);
  return clients.find((client) => String(client.uuid || "").trim().toLowerCase() === target) || null;
}

async function findClientBySubId(subId) {
  const target = String(subId || "").trim();
  if (!target) return null;
  const clients = await getClients().catch(() => []);
  return clients.find((client) => String(client.subId || "").trim() === target) || null;
}

async function getServerStatus() {
  return requestXui("/server/status");
}

async function getXrayLogs(count = 120) {
  try {
    const obj = await requestXui(`/server/logs/${count}`, { method: "POST", body: { level: "info", syslog: false } });
    if (Array.isArray(obj)) return obj.join("\n");
    if (typeof obj === "string") return obj;
    return JSON.stringify(obj, null, 2);
  } catch (error) {
    return `Unable to read logs: ${error.message}`;
  }
}

async function restartXray() {
  return requestXui("/server/restartXrayService", { method: "POST" });
}

async function downloadPanelDb() {
  const buffer = await requestXuiRaw("/server/getDb");
  if (!buffer.length) {
    throw new Error("Panel DB download returned an empty file.");
  }
  if (!buffer.subarray(0, 15).equals(Buffer.from("SQLite format 3"))) {
    let detail = "";
    try {
      detail = JSON.parse(buffer.toString("utf8")).msg || "";
    } catch {
      detail = "";
    }
    throw new Error(detail || "Panel DB download did not return a valid SQLite file.");
  }
  return buffer;
}

module.exports = {
  apiBase,
  getPanelConnectionConfig,
  buildSubLink,
  downloadPanelDb,
  getInbounds,
  normalizeInboundIds,
  getClient,
  getClients,
  getClientLinks,
  getSubLinks,
  getNewUUID,
  createClient,
  updateClient,
  deleteClient,
  findClientByUUID,
  findClientBySubId,
  getServerStatus,
  getXrayLogs,
  restartXray,
};
