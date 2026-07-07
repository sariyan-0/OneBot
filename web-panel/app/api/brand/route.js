import { NextResponse } from "next/server";
import fs from "fs/promises";
import path from "path";
import { getSetting, setSetting } from "../../../lib/db";

export const runtime = "nodejs";

const UPLOAD_DIR = path.join(process.cwd(), "public", "brand");

async function ensureUploadDir() {
  await fs.mkdir(UPLOAD_DIR, { recursive: true });
}

async function fileToUrl(file, prefix) {
  await ensureUploadDir();
  const arrayBuffer = await file.arrayBuffer();
  const ext = path.extname(file.name || "") || ".png";
  const fileName = `${prefix}-${Date.now()}${ext}`;
  const absPath = path.join(UPLOAD_DIR, fileName);
  await fs.writeFile(absPath, Buffer.from(arrayBuffer));
  return `/brand/${fileName}`;
}

export async function GET() {
  const [panelName, tabTitle, logoUrl, faviconUrl] = await Promise.all([
    getSetting("panel_name", "ONEBOT"),
    getSetting("tab_title", ""),
    getSetting("brand_logo_url", ""),
    getSetting("brand_favicon_url", ""),
  ]);

  return NextResponse.json({
    panelName,
    tabTitle,
    logoUrl,
    faviconUrl,
  });
}

export async function POST(request) {
  const contentType = request.headers.get("content-type") || "";
  if (contentType.includes("multipart/form-data")) {
    const form = await request.formData();
    const logo = form.get("logo");
    const favicon = form.get("favicon");
    if (logo && typeof logo === "object" && "arrayBuffer" in logo) {
      const url = await fileToUrl(logo, "logo");
      await setSetting("brand_logo_url", url);
    }
    if (favicon && typeof favicon === "object" && "arrayBuffer" in favicon) {
      const url = await fileToUrl(favicon, "favicon");
      await setSetting("brand_favicon_url", url);
    }
    return NextResponse.json({ ok: true });
  }

  const body = await request.json().catch(() => ({}));
  if (body.panelName != null) {
    await setSetting("panel_name", String(body.panelName || "ONEBOT"));
  }
  if (body.tabTitle != null) {
    await setSetting("tab_title", String(body.tabTitle || ""));
  }
  if (body.logoUrl != null) {
    await setSetting("brand_logo_url", String(body.logoUrl || ""));
  }
  if (body.faviconUrl != null) {
    await setSetting("brand_favicon_url", String(body.faviconUrl || ""));
  }
  return NextResponse.json({ ok: true });
}
