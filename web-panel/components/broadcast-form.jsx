"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

const MAX_TELEGRAM_PHOTO_BYTES = 10 * 1024 * 1024;
const MAX_BROADCAST_UPLOAD_BYTES = 850 * 1024;

function replaceExtension(name, ext) {
  const base = String(name || "broadcast").replace(/\.[^.]+$/, "");
  return `${base || "broadcast"}${ext}`;
}

function loadImage(file) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(url);
      resolve(img);
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("Unable to read the selected image"));
    };
    img.src = url;
  });
}

function canvasToBlob(canvas, type, quality) {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (blob) resolve(blob);
      else reject(new Error("Unable to optimize the selected image"));
    }, type, quality);
  });
}

async function optimizeBroadcastImage(file) {
  if (file.size <= MAX_BROADCAST_UPLOAD_BYTES) {
    return file;
  }

  const img = await loadImage(file);
  let width = img.naturalWidth || img.width;
  let height = img.naturalHeight || img.height;
  if (!width || !height) {
    throw new Error("Unable to read the selected image dimensions");
  }

  const maxSide = 1600;
  const scale = Math.min(1, maxSide / Math.max(width, height));
  width = Math.max(1, Math.round(width * scale));
  height = Math.max(1, Math.round(height * scale));

  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d");
  if (!ctx) {
    throw new Error("Image optimization is not available in this browser");
  }

  let blob = null;
  let quality = 0.86;
  for (let attempt = 0; attempt < 10; attempt += 1) {
    canvas.width = width;
    canvas.height = height;
    ctx.fillStyle = "#fff";
    ctx.fillRect(0, 0, width, height);
    ctx.drawImage(img, 0, 0, width, height);

    blob = await canvasToBlob(canvas, "image/jpeg", quality);
    if (blob.size <= MAX_BROADCAST_UPLOAD_BYTES) {
      break;
    }

    quality = Math.max(0.56, quality - 0.08);
    width = Math.max(1, Math.round(width * 0.84));
    height = Math.max(1, Math.round(height * 0.84));
  }

  if (!blob || blob.size > MAX_BROADCAST_UPLOAD_BYTES) {
    throw new Error("Could not optimize the image enough for upload. Try a smaller image.");
  }

  return new File([blob], replaceExtension(file.name, ".jpg"), {
    type: "image/jpeg",
    lastModified: Date.now(),
  });
}

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
        const uploadImage = await optimizeBroadcastImage(image);
        const uploadForm = new FormData();
        uploadForm.set("message", message);
        uploadForm.set("image", uploadImage, uploadImage.name || "broadcast.jpg");
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
