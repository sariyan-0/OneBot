export const dynamic = "force-dynamic";

import { getSetting } from "../../../lib/db";
import { ImageUp, Sparkles, Trash2, Upload } from "lucide-react";

function BannerCard({ title, description, fileId, previewUrl, children }) {
  return (
    <section className="section" style={{ display: "grid", gap: 14 }}>
      <div className="toolbar" style={{ marginBottom: 0 }}>
        <div>
          <h3 style={{ margin: 0, display: "flex", alignItems: "center", gap: 10 }}>
            <ImageUp size={18} />
            {title}
          </h3>
          <div className="muted">{description}</div>
        </div>
      </div>
      {previewUrl ? (
        <img className="banner-preview" src={previewUrl} alt={`${title} preview`} />
      ) : (
        <div className="notice">No preview image saved yet.</div>
      )}
      <div className="panel" style={{ margin: 0 }}>
        <div className="muted">Telegram file_id</div>
        <code style={{ display: "block", marginTop: 8, wordBreak: "break-all" }}>{fileId || "—"}</code>
      </div>
      {children}
    </section>
  );
}

export default async function BannersPage() {
  const [
    bannerFileId,
    bannerPreviewUrl,
    welcomeBannerFileId,
    welcomeBannerPreviewUrl,
    welcomeBannerCaption,
  ] = await Promise.all([
    getSetting("banner_file_id", ""),
    getSetting("banner_preview_url", ""),
    getSetting("welcome_banner_file_id", ""),
    getSetting("welcome_banner_preview_url", ""),
    getSetting("welcome_banner_caption", ""),
  ]);

  return (
    <div className="grid" style={{ gap: 16 }}>
      <div className="toolbar">
        <div>
          <h2 style={{ margin: 0 }}>Banners</h2>
          <div className="muted">Restore the general bot banner and the first-run welcome banner from the old panel.</div>
        </div>
      </div>

      <div className="two-col">
        <BannerCard
          title="General bot banner"
          description="Shown in normal bot messages that use the shared banner helper."
          fileId={bannerFileId}
          previewUrl={bannerPreviewUrl}
        >
          <form action="/api/banners" method="post" encType="multipart/form-data" className="grid" style={{ gap: 12 }}>
            <div className="form-grid">
              <div className="field-full">
                <label>Upload banner photo</label>
                <input type="file" name="banner_upload" accept="image/*" />
              </div>
            </div>
            <div className="actions">
              <button type="submit"><Upload size={16} /> Save banner</button>
              <button type="submit" className="btn secondary" name="clear_banner" value="1"><Trash2 size={16} /> Clear banner</button>
            </div>
          </form>
        </BannerCard>

        <BannerCard
          title="Welcome banner"
          description="Shown once to new users before /start continues."
          fileId={welcomeBannerFileId}
          previewUrl={welcomeBannerPreviewUrl}
        >
          <form action="/api/banners" method="post" encType="multipart/form-data" className="grid" style={{ gap: 12 }}>
            <div className="form-grid">
              <div className="field-full">
                <label>Upload welcome photo</label>
                <input type="file" name="welcome_banner_upload" accept="image/*" />
              </div>
              <div className="field-full">
                <label>Welcome caption</label>
                <textarea
                  name="welcome_banner_caption"
                  defaultValue={welcomeBannerCaption || ""}
                  placeholder="Caption shown under the welcome banner."
                />
              </div>
            </div>
            <div className="actions">
              <button type="submit"><Sparkles size={16} /> Save welcome banner</button>
              <button type="submit" className="btn secondary" name="clear_welcome_banner" value="1"><Trash2 size={16} /> Clear welcome</button>
            </div>
          </form>
        </BannerCard>
      </div>
    </div>
  );
}
