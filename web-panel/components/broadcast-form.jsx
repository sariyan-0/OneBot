"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

export default function BroadcastForm() {
  const router = useRouter();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  async function onSubmit(event) {
    event.preventDefault();
    setError("");
    setSubmitting(true);

    try {
      const form = new FormData(event.currentTarget);
      const res = await fetch("/api/broadcast/send", {
        method: "POST",
        headers: {
          "X-ONEBOT-CLIENT": "1",
        },
        body: form,
      });

      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.ok === false) {
        throw new Error(data.error || `Request failed (${res.status})`);
      }

      router.refresh();
      event.currentTarget.reset();
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

      <button type="submit" disabled={submitting}>
        {submitting ? "Sending..." : "Send broadcast"}
      </button>
    </form>
  );
}
