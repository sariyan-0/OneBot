"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import TicketMessageList from "./ticket-message-list";

function formatTime(value) {
  if (!value) return "";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  return new Intl.DateTimeFormat("en-GB", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(d);
}

export default function TicketThreadClient({ ticketId, owner, customerHref, customerTag, initialMessages, initialStatus }) {
  const [messages, setMessages] = useState(initialMessages);
  const [status, setStatus] = useState(initialStatus);
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const lastSyncRef = useRef(
    initialMessages.map((msg) => `${msg.id}:${msg.body}:${msg.is_admin_reply ? 1 : 0}`).join("|")
  );

  const isClosed = status === "closed";
  const thread = useMemo(() => messages, [messages]);

  useEffect(() => {
    let cancelled = false;
    let timer = null;

    async function syncThread() {
      if (typeof document !== "undefined" && document.hidden) {
        return;
      }
      try {
        const res = await fetch(`/api/tickets/${ticketId}`, {
          headers: {
            Accept: "application/json",
            "x-onebot-client": "ticket-thread",
          },
          credentials: "same-origin",
          cache: "no-store",
        });
        if (!res.ok) return;
        const data = await res.json().catch(() => null);
        if (!data?.ok || cancelled) return;

        const remoteMessages = Array.isArray(data.messages) ? data.messages : [];
        const remoteSignature = remoteMessages.map((msg) => `${msg.id}:${msg.body}:${msg.is_admin_reply ? 1 : 0}`).join("|");
        if (remoteSignature !== lastSyncRef.current) {
          lastSyncRef.current = remoteSignature;
          setMessages(
            remoteMessages.map((msg, index) => ({
              ...msg,
              side: msg.is_admin_reply ? "right" : "left",
              kind: msg.is_admin_reply ? "support" : "customer",
              initials: msg.is_admin_reply ? "AD" : (owner?.[0] || "U").toUpperCase(),
              isFirst: index === 0,
              timeLabel: formatTime(msg.created_at),
            }))
          );
        }
        if (data.ticket?.status && data.ticket.status !== status) {
          setStatus(data.ticket.status);
        }
      } catch {
        // polling should never interrupt the UI
      }
    }

    syncThread();
    timer = setInterval(syncThread, 5000);
    function handleVisibilityChange() {
      if (!document.hidden) {
        syncThread();
      }
    }
    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", handleVisibilityChange);
    }
    return () => {
      cancelled = true;
      if (timer) clearInterval(timer);
      if (typeof document !== "undefined") {
        document.removeEventListener("visibilitychange", handleVisibilityChange);
      }
    };
  }, [owner, ticketId]);

  async function postAction(action, payload = {}) {
    setBusy(true);
    setError("");
    try {
      const form = new FormData();
      form.set("action", action);
      for (const [key, value] of Object.entries(payload)) {
        form.set(key, value);
      }

      const res = await fetch(`/api/tickets/${ticketId}`, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "x-onebot-client": "ticket-thread",
        },
        body: form,
      });

      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) {
        throw new Error(data.error || "Request failed");
      }

      if (data.status) setStatus(data.status);
      if (data.message) {
        const nextMessage = {
          ...data.message,
          side: "right",
          initials: "AD",
          is_admin_reply: 1,
          timeLabel: formatTime(data.message.created_at),
        };
        lastSyncRef.current = `${nextMessage.id}:${nextMessage.body}:1`;
        setMessages((prev) => [...prev, nextMessage]);
      }
      return data;
    } finally {
      setBusy(false);
    }
  }

  async function handleReply(event) {
    event.preventDefault();
    const text = body.trim();
    if (!text) return;
    try {
      await postAction("reply", { body: text });
      setBody("");
    } catch (err) {
      setError(err.message || "Unable to send reply");
    }
  }

  return (
    <div className="ticket-thread panel">
      <div className="ticket-thread-head">
        <div>
          <h3 className="item-title" style={{ marginBottom: 2 }}>Conversation</h3>
          <div className="muted">Messages stay inside the thread and the page does not jump after send.</div>
          {customerHref ? (
            <Link href={customerHref} className="ticket-customer-link ticket-customer-link--inline">
              <span>{owner}</span>
              <span className="ticket-dot" />
              <span>{customerTag}</span>
            </Link>
          ) : null}
        </div>
        <div className="ticket-thread-head-actions">
          <span className={`pill ${isClosed ? "bad" : "ok"}`}>{isClosed ? "Closed" : "Open"}</span>
        </div>
      </div>

      <TicketMessageList thread={thread} owner={owner} />

      <div className="ticket-composer">
        {error ? <div className="error">{error}</div> : null}

        <div className="ticket-status-row">
          <span className={`pill ${isClosed ? "bad" : "warn"}`}>
            {isClosed ? "Closed" : "Open"}
          </span>
          <div className="actions">
            {isClosed ? (
              <button className="btn secondary" type="button" disabled={busy} onClick={() => postAction("reopen")}>
                Reopen ticket
              </button>
            ) : (
              <button className="btn danger" type="button" disabled={busy} onClick={() => postAction("close")}>
                Close ticket
              </button>
            )}
          </div>
        </div>

        <form className="ticket-compose-form" onSubmit={handleReply}>
          <textarea
            name="body"
            value={body}
            onChange={(e) => setBody(e.target.value)}
            placeholder="Write a reply to the customer..."
            rows={4}
            required
            disabled={busy}
          />
          <div className="ticket-compose-actions">
            <div className="muted">This stays in the chat pane and does not reload the page.</div>
            <div className="actions">
              <button className="btn secondary" type="submit" disabled={busy || !body.trim()}>
                Send reply
              </button>
            </div>
          </div>
        </form>
      </div>
    </div>
  );
}
