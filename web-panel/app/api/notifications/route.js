import { NextResponse } from "next/server";
import { getDashboardStats } from "../../../lib/admin-data";

export async function GET() {
  const stats = await getDashboardStats();
  const items = [
    ...stats.recentPayments.slice(0, 4).map((payment) => ({
      type: "payment",
      label: `${payment.payment_method} · ${payment.order_id} · $${payment.amount_usdt}`,
      time: payment.created_at,
      link: "/admin/payments",
    })),
    ...stats.recentActivity.slice(0, 4).map((item) => ({
      type: item.direction === "outgoing" ? "message" : "activity",
      label: item.text || item.event_type.replaceAll("_", " "),
      time: item.created_at,
      link: "/admin/activity",
    })),
  ].slice(0, 8);

  return NextResponse.json({
    total: items.length,
    pendingPayments: stats.pendingPayments,
    openTickets: stats.tickets,
    expiringSoon: 0,
    source: "live",
    items,
  });
}
