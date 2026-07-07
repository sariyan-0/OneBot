import archiver from "archiver";
import fs from "fs";
import path from "path";
import { getRootDir } from "../../../../lib/root";
import { getSettingsMap, sqlitePath } from "../../../../lib/db";
import { downloadPanelDb } from "../../../../lib/xui";

function addIfExists(zip, absPath, name) {
  if (fs.existsSync(absPath)) {
    zip.file(absPath, { name });
  }
}

export async function POST() {
  const root = getRootDir();
  const dbFile = sqlitePath();
  const archive = archiver("zip", { zlib: { level: 9 } });
  const chunks = [];

  archive.on("data", (chunk) => chunks.push(chunk));
  const finished = new Promise((resolve, reject) => {
    archive.on("end", resolve);
    archive.on("error", reject);
  });

  const envPath = path.join(root, ".env");
  const panelEnvLocalPath = path.join(root, "web-panel", ".env.local");
  const dbPath = dbFile || path.join(root, "bot_data.db");
  const dbWalPath = `${dbPath}-wal`;
  const dbShmPath = `${dbPath}-shm`;
  const logsDir = path.join(root, "logs");
  const brandDir = path.join(root, "web-panel", "public", "brand");
  const bannersDir = path.join(root, "web-panel", "public", "banners");
  const nginxPath = "/etc/nginx/conf.d/onebot-webhook.conf";
  const manifest = {
    createdAt: new Date().toISOString(),
    root,
    includes: [],
    warnings: [],
  };

  if (fs.existsSync(envPath)) {
    archive.file(envPath, { name: ".env" });
    manifest.includes.push(".env");
  }
  if (fs.existsSync(panelEnvLocalPath)) {
    archive.file(panelEnvLocalPath, { name: "web-panel/.env.local" });
    manifest.includes.push("web-panel/.env.local");
  }

  try {
    const settings = await getSettingsMap();
    archive.append(JSON.stringify(settings, null, 2), { name: "admin-settings.json" });
    manifest.includes.push("admin-settings.json");
  } catch (error) {
    manifest.warnings.push(`admin-settings.json was skipped: ${error.message}`);
  }

  if (fs.existsSync(dbPath)) {
    archive.file(dbPath, { name: "bot_data.db" });
    manifest.includes.push("bot_data.db");
  }
  if (fs.existsSync(dbWalPath)) {
    archive.file(dbWalPath, { name: "bot_data.db-wal" });
    manifest.includes.push("bot_data.db-wal");
  }
  if (fs.existsSync(dbShmPath)) {
    archive.file(dbShmPath, { name: "bot_data.db-shm" });
    manifest.includes.push("bot_data.db-shm");
  }
  if (fs.existsSync(logsDir)) {
    archive.directory(logsDir, "logs");
    manifest.includes.push("logs/");
  }
  if (fs.existsSync(brandDir)) {
    archive.directory(brandDir, "web-panel/public/brand");
    manifest.includes.push("web-panel/public/brand/");
  }
  if (fs.existsSync(bannersDir)) {
    archive.directory(bannersDir, "web-panel/public/banners");
    manifest.includes.push("web-panel/public/banners/");
  }
  if (fs.existsSync(nginxPath)) {
    archive.file(nginxPath, { name: "nginx/onebot-webhook.conf" });
    manifest.includes.push("nginx/onebot-webhook.conf");
  }

  try {
    const panelDb = await downloadPanelDb();
    archive.append(panelDb, { name: "xui-panel.db" });
    manifest.includes.push("xui-panel.db");
  } catch (error) {
    manifest.warnings.push(`xui-panel.db was skipped: ${error.message}`);
  }

  archive.append(JSON.stringify(manifest, null, 2), { name: "backup-manifest.json" });

  archive.finalize();
  await finished;

  return new Response(Buffer.concat(chunks), {
    headers: {
      "Content-Type": "application/zip",
      "Content-Disposition": `attachment; filename="onebot-backup-${Date.now()}.zip"`,
    },
  });
}
