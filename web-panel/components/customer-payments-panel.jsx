"use client";

import { useMemo, useState } from "react";
import { Search } from "lucide-react";

import { fmtDate, money } from "../lib/format";

function isCardPayment(payment) {
  const method = String(payment?.payment_method || "").toLowerCase();
  return method === "card" || method.includes("card");
}

function tomanText(payment) {
  if (!payment?.amount_rial) return "";
  return `${Number(payment.amount_rial / 10).toLocaleString()} toman`;
}

function amountSummary(payment) {
  const method = String(payment?.payment_method || "").toLowerCase();
  if (method.startsWith("wallet_toman")) {
    return tomanText(payment) || "Toman wallet payment";
  }
  if (method.startsWith("wallet_usd")) {
    return `$${money(payment.amount_usdt)}`;
  }
  if (isCardPayment(payment)) {
    return tomanText(payment) || "Card payment";
  }
  return `$${money(payment.amount_usdt)}`;
}

export default function CustomerPaymentsPanel({ payments }) {
  const [query, setQuery] = useState("");
  const needle = query.trim().toLowerCase();

  const visiblePayments = useMemo(() => {
    if (!needle) {
      return payments.slice(0, 3);
    }
    return payments.filter((payment) => {
      const orderId = String(payment.order_id || "").toLowerCase();
      const paymentId = String(payment.payment_id || "").toLowerCase();
      return orderId.includes(needle) || paymentId.includes(needle);
    });
  }, [needle, payments]);

  return (
    <div className="grid" style={{ gap: 12 }}>
      <div className="toolbar" style={{ marginBottom: 0 }}>
        <div>
          <h2 style={{ margin: 0 }}>Payments</h2>
          <div className="muted">
            {needle
              ? `Showing ${visiblePayments.length} matching payments.`
              : `Showing 3 most recent payments out of ${payments.length}.`}
          </div>
        </div>
      </div>

      <div style={{ position: "relative" }}>
        <Search size={16} style={{ position: "absolute", right: 12, top: 12, opacity: 0.5 }} />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search invoice / order id..."
          style={{ paddingRight: 36 }}
        />
      </div>

      <div className="card-list">
        {visiblePayments.map((payment) => (
          <div key={payment.id} className="item">
            <div className="item-head">
              <div>
                <h3 className="item-title wrap">{payment.order_id}</h3>
                <p className="item-sub wrap">
                  {amountSummary(payment)} · {payment.payment_method} · {payment.status}
                </p>
                <p className="item-sub">{fmtDate(payment.created_at)}</p>
              </div>
              <span className={`pill ${payment.status === "confirmed" ? "ok" : payment.status === "awaiting_review" ? "warn" : "bad"}`}>
                {payment.status}
              </span>
            </div>
          </div>
        ))}
        {!visiblePayments.length ? <div className="muted">No payments matched that invoice id.</div> : null}
      </div>
    </div>
  );
}
