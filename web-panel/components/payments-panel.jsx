"use client";

import { useMemo, useState, useTransition } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ChevronDown, ChevronUp, Search } from "lucide-react";

import { fmtDate, money } from "../lib/format";

function statusClass(status) {
  if (status === "confirmed" || status === "finished") return "ok";
  if (status === "awaiting_review" || status === "waiting" || status === "confirming" || status === "pending") return "warn";
  return "bad";
}

function tomanText(payment) {
  if (!payment?.amount_rial) return "";
  return `${Number(payment.amount_rial / 10).toLocaleString()} toman`;
}

function isCardPayment(payment) {
  const method = String(payment?.payment_method || "").toLowerCase();
  return method === "card" || method.includes("card");
}

function amountSummary(payment) {
  const toman = tomanText(payment);
  const method = String(payment?.payment_method || "").toLowerCase();
  if (method.startsWith("wallet_toman")) {
    return toman || "Toman wallet payment";
  }
  if (method.startsWith("wallet_usd")) {
    return `$${money(payment.amount_usdt)}`;
  }
  if (isCardPayment(payment)) {
    return toman || "Card payment";
  }
  return `${`$${money(payment.amount_usdt)}`}${toman ? ` · ${toman}` : ""}`;
}

export default function PaymentsPanel({ payments }) {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [openId, setOpenId] = useState(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState(null);
  const [pending, startTransition] = useTransition();
  const needle = query.trim().toLowerCase();

  async function reviewPayment(paymentId, action) {
    setMessage("");
    setError("");
    setBusyId(paymentId);
    try {
      const res = await fetch(`/api/payments/${paymentId}/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.ok === false) {
        throw new Error(data.error || `Request failed (${res.status})`);
      }
      setMessage(action === "approve" ? "Payment approved." : "Payment rejected.");
      startTransition(() => {
        router.refresh();
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Request failed");
    } finally {
      setBusyId(null);
    }
  }

  const filtered = useMemo(() => {
    if (!needle) return payments;
    return payments.filter((payment) => {
      const orderId = String(payment.order_id || "").toLowerCase();
      const paymentId = String(payment.payment_id || "").toLowerCase();
      const username = String(payment.username || "").toLowerCase();
      const telegram = String(payment.telegram_id || "");
      return (
        orderId.includes(needle) ||
        paymentId.includes(needle) ||
        username.includes(needle) ||
        telegram.includes(needle)
      );
    });
  }, [needle, payments]);

  return (
    <div className="grid" style={{ gap: 16 }}>
      {error ? <div className="notice error">{error}</div> : null}
      {message ? <div className="notice">{message}</div> : null}
      <div className="toolbar">
        <div>
          <h2 style={{ margin: 0 }}>Payments</h2>
          <div className="muted">Payments, card receipts, and receipt details in one place.</div>
        </div>
      </div>

      <div style={{ position: "relative" }}>
        <Search size={16} style={{ position: "absolute", right: 12, top: 12, opacity: 0.5 }} />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search invoice / order id, username, or telegram id..."
          style={{ paddingRight: 36 }}
        />
      </div>

      <div className="card-list">
        {filtered.map((payment) => {
          const isOpen = openId === payment.id;
          const hasReceipt = Boolean(payment.receipt_file_id);

          return (
            <div key={payment.id} className="item">
              <div className="item-head">
                <div>
                  <h3 className="item-title wrap">{payment.order_id}</h3>
                  <p className="item-sub wrap">
                    {amountSummary(payment)}{" · "}{payment.payment_method} · {fmtDate(payment.created_at)}
                  </p>
                  <p className="item-sub wrap">
                    <Link href={`/admin/customers/${payment.user_id}`} className="wrap">
                      {payment.first_name || payment.username || payment.telegram_id}
                    </Link>
                    {payment.payment_id ? ` · invoice ${payment.payment_id}` : ""}
                  </p>
                </div>
                <div className="actions">
                  <span className={`pill ${statusClass(payment.status)}`}>{payment.status}</span>
                  {(hasReceipt || payment.receipt_type === "text") ? (
                    <button
                      type="button"
                      className="btn secondary"
                      onClick={() => setOpenId(isOpen ? null : payment.id)}
                    >
                      {isOpen ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                      Details
                    </button>
                  ) : null}
                </div>
              </div>

              {isOpen ? (
                <div className="grid" style={{ gap: 12, marginTop: 14 }}>
                  <div className="grid cards" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))" }}>
                    <div className="stat" style={{ minHeight: 0 }}>
                      <span className="muted">Method</span>
                      <strong style={{ fontSize: 18 }}>{payment.payment_method}</strong>
                    </div>
                    <div className="stat" style={{ minHeight: 0 }}>
                      <span className="muted">Status</span>
                      <strong style={{ fontSize: 18 }}>{payment.status}</strong>
                    </div>
                    <div className="stat" style={{ minHeight: 0 }}>
                      <span className="muted">Amount</span>
                      <strong style={{ fontSize: 18 }}>
                        {String(payment?.payment_method || "").toLowerCase().startsWith("wallet_toman")
                          ? (tomanText(payment) || "Toman wallet payment")
                          : isCardPayment(payment)
                            ? (tomanText(payment) || "Card payment")
                            : `$${money(payment.amount_usdt)}`}
                      </strong>
                      <div className="muted">
                        {String(payment?.payment_method || "").toLowerCase().startsWith("wallet_toman")
                          ? "Wallet spend in toman"
                          : isCardPayment(payment)
                          ? "Card to card"
                          : (payment.amount_rial ? tomanText(payment) : "No toman value")}
                      </div>
                    </div>
                  </div>

                  {isCardPayment(payment) && payment.status === "awaiting_review" ? (
                    <div className="actions">
                      <button
                        type="button"
                        className="btn"
                        disabled={pending || busyId === payment.id}
                        onClick={() => reviewPayment(payment.id, "approve")}
                      >
                        Approve
                      </button>
                      <button
                        type="button"
                        className="btn secondary"
                        disabled={pending || busyId === payment.id}
                        onClick={() => reviewPayment(payment.id, "reject")}
                      >
                        Reject
                      </button>
                    </div>
                  ) : null}

                  {payment.receipt_type === "photo" && payment.receipt_file_id ? (
                    <div className="grid" style={{ gap: 8 }}>
                      <div className="muted">Receipt image</div>
                      <img
                        className="receipt"
                        src={`/api/payments/${payment.id}/receipt`}
                        alt={`Receipt ${payment.order_id}`}
                      />
                    </div>
                  ) : null}

                  {payment.receipt_type === "text" && payment.receipt_file_id ? (
                    <div className="grid" style={{ gap: 8 }}>
                      <div className="muted">Receipt text</div>
                      <div className="item-sub wrap" style={{ whiteSpace: "pre-wrap" }}>
                        {payment.receipt_file_id}
                      </div>
                    </div>
                  ) : null}
                </div>
              ) : null}
            </div>
          );
        })}
        {!filtered.length ? <div className="muted">No payments matched your search.</div> : null}
      </div>
    </div>
  );
}
