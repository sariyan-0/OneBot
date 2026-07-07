"use client";

import { useEffect, useRef, useState } from "react";
import { ImagePlus, Palette, Upload, X } from "lucide-react";

async function jsonFetch(url, init) {
  const res = await fetch(url, init);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

export default function BrandPage() {
  const [panelName, setPanelName] = useState("ONEBOT");
  const [tabTitle, setTabTitle] = useState("");
  const [logoUrl, setLogoUrl] = useState("");
  const [faviconUrl, setFaviconUrl] = useState("");
  const [bannerUrl, setBannerUrl] = useState("");
  const [welcomeBannerUrl, setWelcomeBannerUrl] = useState("");
  const [welcomeBannerCaption, setWelcomeBannerCaption] = useState("");
  const [logoFile, setLogoFile] = useState(null);
  const [faviconFile, setFaviconFile] = useState(null);
  const [bannerFile, setBannerFile] = useState(null);
  const [welcomeBannerFile, setWelcomeBannerFile] = useState(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [bannerSaving, setBannerSaving] = useState(false);
  const [bannerSaved, setBannerSaved] = useState(false);
  const logoRef = useRef(null);
  const faviconRef = useRef(null);
  const bannerRef = useRef(null);
  const welcomeBannerRef = useRef(null);

  useEffect(() => {
    Promise.all([jsonFetch("/api/brand"), jsonFetch("/api/banners")])
      .then(([brandData, bannerData]) => {
        const data = brandData || {};
        const banners = bannerData || {};
        setPanelName(data.panelName || "ONEBOT");
        setTabTitle(data.tabTitle || "");
        setLogoUrl(data.logoUrl || "");
        setFaviconUrl(data.faviconUrl || "");
        setBannerUrl(banners.bannerPreviewUrl || "");
        setWelcomeBannerUrl(banners.welcomeBannerPreviewUrl || "");
        setWelcomeBannerCaption(banners.welcomeBannerCaption || "");
      })
      .catch(() => {});
  }, []);

  const handleSave = async () => {
    setSaving(true);
    setSaved(false);
    try {
      if (logoFile || faviconFile) {
        const form = new FormData();
        if (logoFile) form.append("logo", logoFile);
        if (faviconFile) form.append("favicon", faviconFile);
        await jsonFetch("/api/brand", { method: "POST", body: form });
      }
      await jsonFetch("/api/brand", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ panelName, tabTitle }),
      });
      setSaved(true);
    } finally {
      setSaving(false);
    }
  };

  const handleBannerSave = async () => {
    setBannerSaving(true);
    setBannerSaved(false);
    try {
      const form = new FormData();
      if (bannerFile) form.append("banner_upload", bannerFile);
      if (welcomeBannerFile) form.append("welcome_banner_upload", welcomeBannerFile);
      form.append("welcome_banner_caption", welcomeBannerCaption || "");
      await fetch("/api/banners", { method: "POST", body: form });
      const refreshed = await jsonFetch("/api/banners");
      setBannerUrl(refreshed.bannerPreviewUrl || "");
      setWelcomeBannerUrl(refreshed.welcomeBannerPreviewUrl || "");
      setWelcomeBannerCaption(refreshed.welcomeBannerCaption || "");
      setBannerFile(null);
      setWelcomeBannerFile(null);
      if (bannerRef.current) bannerRef.current.value = "";
      if (welcomeBannerRef.current) welcomeBannerRef.current.value = "";
      setBannerSaved(true);
    } finally {
      setBannerSaving(false);
    }
  };

  return (
    <div className="grid" style={{ gap: 16, maxWidth: 960 }}>
      <div className="toolbar">
        <div>
          <h2 style={{ margin: 0 }}>Brand</h2>
          <div className="muted">Rename the panel and upload the logo and favicon used in the UI.</div>
        </div>
      </div>

      <section className="section">
        <div className="form-grid">
          <div>
            <label>Panel name</label>
            <input value={panelName} onChange={(e) => setPanelName(e.target.value)} placeholder="ONEBOT" />
          </div>
          <div>
            <label>Browser tab title</label>
            <input value={tabTitle} onChange={(e) => setTabTitle(e.target.value)} placeholder="ONEBOT Admin Panel" />
          </div>
          <div>
            <label>Sidebar logo</label>
            <div className="actions" style={{ marginTop: 8 }}>
              {logoUrl && <img src={logoUrl} alt="logo" style={{ width: 42, height: 42, objectFit: "cover", borderRadius: 12, border: "1px solid var(--line)" }} />}
              <input ref={logoRef} type="file" accept="image/*" hidden onChange={(e) => setLogoFile(e.target.files?.[0] || null)} />
              <button type="button" className="btn secondary" onClick={() => logoRef.current?.click()}>
                <Upload size={16} />
                {logoFile ? logoFile.name : "Upload"}
              </button>
              {logoFile && <button type="button" className="btn secondary" onClick={() => { setLogoFile(null); if (logoRef.current) logoRef.current.value = ""; }}><X size={16} /></button>}
            </div>
          </div>
          <div>
            <label>Favicon</label>
            <div className="actions" style={{ marginTop: 8 }}>
              {faviconUrl && <img src={faviconUrl} alt="favicon" style={{ width: 32, height: 32, objectFit: "cover", borderRadius: 10, border: "1px solid var(--line)" }} />}
              <input ref={faviconRef} type="file" accept="image/*,.ico" hidden onChange={(e) => setFaviconFile(e.target.files?.[0] || null)} />
              <button type="button" className="btn secondary" onClick={() => faviconRef.current?.click()}>
                <ImagePlus size={16} />
                {faviconFile ? faviconFile.name : "Upload"}
              </button>
              {faviconFile && <button type="button" className="btn secondary" onClick={() => { setFaviconFile(null); if (faviconRef.current) faviconRef.current.value = ""; }}><X size={16} /></button>}
            </div>
          </div>
        </div>
        <div className="actions" style={{ marginTop: 16 }}>
          <button type="button" onClick={handleSave} disabled={saving}>
            <Palette size={16} />
            {saved ? "Saved" : saving ? "Saving..." : "Save branding"}
          </button>
        </div>
      </section>

      <section className="section">
        <div className="toolbar" style={{ marginBottom: 10 }}>
          <div>
            <h3 style={{ margin: 0, display: "flex", alignItems: "center", gap: 8 }}>
              <ImagePlus size={18} />
              Banners
            </h3>
            <div className="muted">General banner for bot messages and the welcome banner shown to new users.</div>
          </div>
        </div>
        <div className="two-col">
          <div className="grid">
            <div className="notice" style={{ display: "grid", gap: 10 }}>
              <label>General banner</label>
              {bannerUrl ? <img src={bannerUrl} alt="general banner" className="banner-preview" /> : <div className="muted">No banner uploaded.</div>}
              <input ref={bannerRef} type="file" accept="image/*" hidden onChange={(e) => setBannerFile(e.target.files?.[0] || null)} />
              <div className="actions">
                <button type="button" className="btn secondary" onClick={() => bannerRef.current?.click()}>
                  <Upload size={16} />
                  {bannerFile ? bannerFile.name : "Upload banner"}
                </button>
                {bannerFile && (
                  <button type="button" className="btn secondary" onClick={() => { setBannerFile(null); if (bannerRef.current) bannerRef.current.value = ""; }}>
                    <X size={16} />
                  </button>
                )}
              </div>
            </div>
          </div>
          <div className="grid">
            <div className="notice" style={{ display: "grid", gap: 10 }}>
              <label>Welcome banner</label>
              {welcomeBannerUrl ? <img src={welcomeBannerUrl} alt="welcome banner" className="banner-preview" /> : <div className="muted">No welcome banner uploaded.</div>}
              <input ref={welcomeBannerRef} type="file" accept="image/*" hidden onChange={(e) => setWelcomeBannerFile(e.target.files?.[0] || null)} />
              <div className="actions">
                <button type="button" className="btn secondary" onClick={() => welcomeBannerRef.current?.click()}>
                  <Upload size={16} />
                  {welcomeBannerFile ? welcomeBannerFile.name : "Upload welcome"}
                </button>
                {welcomeBannerFile && (
                  <button type="button" className="btn secondary" onClick={() => { setWelcomeBannerFile(null); if (welcomeBannerRef.current) welcomeBannerRef.current.value = ""; }}>
                    <X size={16} />
                  </button>
                )}
              </div>
              <div>
                <label>Welcome caption</label>
                <textarea value={welcomeBannerCaption} onChange={(e) => setWelcomeBannerCaption(e.target.value)} placeholder="Caption shown under the welcome banner." />
              </div>
            </div>
          </div>
        </div>
        <div className="actions" style={{ marginTop: 16 }}>
          <button type="button" onClick={handleBannerSave} disabled={bannerSaving}>
            <Palette size={16} />
            {bannerSaved ? "Saved" : bannerSaving ? "Saving..." : "Save banners"}
          </button>
          <a className="btn secondary" href="/admin/banners">Open full banner manager</a>
        </div>
      </section>
    </div>
  );
}
