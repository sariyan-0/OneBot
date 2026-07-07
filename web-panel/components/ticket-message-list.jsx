"use client";

import { useEffect, useRef } from "react";

export default function TicketMessageList({ thread, owner }) {
  const listRef = useRef(null);

  useEffect(() => {
    const el = listRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [thread.length]);

  return (
    <div className="ticket-message-list" ref={listRef} aria-live="polite">
      {thread.map((msg) => (
        <article key={msg.id} className={`ticket-message ${msg.side}`}>
          <div className="ticket-avatar">{msg.initials}</div>
          <div className="ticket-bubble">
            <div className="ticket-bubble-head">
              <strong>{msg.is_admin_reply ? "Support" : owner}</strong>
              <span>{msg.timeLabel}</span>
            </div>
            <div className="ticket-bubble-body">{msg.body}</div>
          </div>
        </article>
      ))}
    </div>
  );
}
