export const dynamic = "force-dynamic";

import { getPayments } from "../../../lib/admin-data";
import PaymentsPanel from "../../../components/payments-panel";

export default async function PaymentsPage() {
  const payments = await getPayments(250);
  return <PaymentsPanel payments={payments} />;
}
