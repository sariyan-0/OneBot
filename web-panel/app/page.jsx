export const dynamic = "force-dynamic";

import { redirect } from "next/navigation";
import { cookies } from "next/headers";
import { isAdminAuth } from "../lib/auth";

export default async function Home() {
  const store = cookies();
  if (await isAdminAuth(store)) {
    redirect("/admin");
  }
  redirect("/login");
}
