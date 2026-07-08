import { NextResponse } from "next/server";
import fs from "fs";
import path from "path";
import AdmZip from "adm-zip";
import { getRootDir } from "../../../../lib/root";
import { redirectSeeOther } from "../../../../lib/redirect";
import { closeSqlite, getSetting, setSetting, sqlitePath } from "../../../../lib/db";

export const runtime = "nodejs";

const RESTORE_PATHS = new Map([
  ["bot_data.db", "bot_data.db"],
  ["bot_data.db-wal", "bot_data.db-wal"],
  ["bot_data.db-shm", "bot_data.db-shm"],
  ["nginx/onebot-webhook.conf", "nginx/onebot-webhook.conf"],
]);

const PRESERVE_SETTING_KEYS = new Set([
  "BOT_TOKEN",
  "BOT_USERNAME",
]);

const ENV_BACKUP_PATHS = new Set([
  ".env",
  "web-panel/.env.local",
]);

const RESTORE_PREFIXES = [
  "logs/",
  "web-panel/public/brand/",
  "web-panel/public/banners/",
];

function resolveRestoreTarget(root, entryName) {
  const normalized = String(entryName || "").replace(/\\/g, "/");
  const direct = RESTORE_PATHS.get(normalized);
  if (direct) {
    return path.join(root, direct);
  }
  const baseName = path.basename(normalized);
  if (/^bot_data_\d{8}_\d{4}\.db$/i.test(baseName)) {
    return path.join(root, "bot_data.db");
  }

  const prefix = RESTORE_PREFIXES.find((item) => normalized.startsWith(item));
  if (!prefix) {
    return null;
  }

  return path.join(root, normalized);
}

function syncSqliteCompanion(root, dbFile, entryName) {
  const source = path.join(root, entryName);
  const destination = entryName === "bot_data.db" ? dbFile : `${dbFile}${entryName.slice("bot_data.db".length)}`;
  if (!fs.existsSync(source)) {
    return;
  }
  fs.mkdirSync(path.dirname(destination), { recursive: true });
  fs.copyFileSync(source, destination);
}

async function readCurrentDeploymentSettings() {
  const values = {};
  for (const key of PRESERVE_SETTING_KEYS) {
    const dbValue = await getSetting(key, "").catch(() => "");
    const envValue = String(process.env[key] || "").trim();
    const value = String(dbValue || envValue || "").trim();
    if (value) values[key] = value;
  }
  return values;
}

function hasDbEntries(zip) {
  return zip.getEntries().some((entry) => (
    !entry.isDirectory
    && (
      ["bot_data.db", "bot_data.db-wal", "bot_data.db-shm"].includes(entry.entryName)
      || /^bot_data_\d{8}_\d{4}\.db$/i.test(path.basename(entry.entryName))
    )
  ));
}

export async function POST(request) {
  const form = await request.formData();
  const file = form.get("archive");
  if (!file || typeof file.arrayBuffer !== "function") {
    return NextResponse.json({ ok: false, error: "Missing zip archive" }, { status: 400 });
  }

  const root = getRootDir();
  const dbFile = sqlitePath();
  const temp = path.join(root, `.restore-${Date.now()}.zip`);
  const buffer = Buffer.from(await file.arrayBuffer());
  fs.writeFileSync(temp, buffer);

  try {
    const zip = new AdmZip(temp);
    const preservedSettings = await readCurrentDeploymentSettings();
    const adminSettingsEntry = zip.getEntry("admin-settings.json");
    let importedSettings = null;
    if (adminSettingsEntry && !adminSettingsEntry.isDirectory) {
      try {
        const settings = JSON.parse(adminSettingsEntry.getData().toString("utf8"));
        if (settings && typeof settings === "object") {
          importedSettings = settings;
        }
      } catch {
        importedSettings = null;
      }
    }

    if (hasDbEntries(zip)) {
      closeSqlite();
    }

    zip.getEntries().forEach((entry) => {
      if (entry.isDirectory) return;
      if (entry.entryName === "admin-settings.json") return;
      if (ENV_BACKUP_PATHS.has(entry.entryName)) return;
      const target = resolveRestoreTarget(root, entry.entryName);
      if (!target) return;
      const resolved = path.resolve(target);
      if (!resolved.startsWith(root)) return;
      fs.mkdirSync(path.dirname(target), { recursive: true });
      fs.writeFileSync(target, entry.getData());
    });
    if (dbFile && dbFile !== path.join(root, "bot_data.db")) {
      syncSqliteCompanion(root, dbFile, "bot_data.db");
      syncSqliteCompanion(root, dbFile, "bot_data.db-wal");
      syncSqliteCompanion(root, dbFile, "bot_data.db-shm");
    }

    if (importedSettings) {
      for (const [key, value] of Object.entries(importedSettings)) {
        if (PRESERVE_SETTING_KEYS.has(key) && preservedSettings[key]) continue;
        await setSetting(key, value == null ? "" : String(value));
      }
    }
    for (const [key, value] of Object.entries(preservedSettings)) {
      await setSetting(key, value);
    }
    fs.writeFileSync(path.join(root, ".onebot-restart"), String(Date.now()), "utf8");
  } finally {
    fs.rmSync(temp, { force: true });
  }

  return redirectSeeOther(request, "/admin/backups");
}
