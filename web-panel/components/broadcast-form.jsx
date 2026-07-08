"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

const MAX_TELEGRAM_PHOTO_BYTES = 10 * 1024 * 1024;

export default function BroadcastForm() {
  const router = useRouter();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function onSubmit(event) {
    event.preventDefault();
    const formEl = event.currentTarget;
    setError("");
    setNotice("");
    setSubmitting(true);

    try {
      const form = new FormData(formEl);
      const image = form.get("image");
      const hasImage = image && typeof image === "object" && "size" in image && image.size > 0;
      if (hasImage && image.size > MAX_TELEGRAM_PHOTO_BYTES) {
        throw new Error("Broadcast image is too large. Maximum size is 10 MB");
      }

      const message = String(form.get("message") || "").trim();
      let res;
      if (hasImage) {
        const uploadForm = new FormData();
        uploadForm.set("message", message);
        uploadForm.set("image", image, image.name || "broadcast.png");
        res = await fetch("/api/broadcast/send", {
            method: "POST",
            headers: {
              "X-ONEBOT-CLIENT": "1",
            },
            body: uploadForm,
          });
      } else {
        res = await fetch("/api/broadcast/send", {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-ONEBOT-CLIENT": "1",
            },
            body: JSON.stringify({ message }),
          });
      }

      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.ok === false) {
        throw new Error(data.error || `Request failed (${res.status})`);
      }

      router.refresh();
      formEl.reset();
      setNotice(`Broadcast complete: ${data.sent || 0} sent${data.failed ? `, ${data.failed} failed` : ""}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Broadcast failed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={onSubmit} encType="multipart/form-data" className="grid" style={{ gap: 12 }}>
      <div className="form-grid">
        <div className="field-full">
          <label>Message</label>
          <textarea name="message" placeholder="Broadcast text..." required />
        </div>
        <div className="field-full">
          <label>Optional image</label>
          <input type="file" name="image" accept="image/*" />
        </div>
      </div>

      {error ? <div className="notice error">{error}</div> : null}
      {notice ? <div className="notice success">{notice}</div> : null}

      <button type="submit" disabled={submitting}>
        {submitting ? "Sending..." : "Send broadcast"}
      </button>
    </form>
  );
}
