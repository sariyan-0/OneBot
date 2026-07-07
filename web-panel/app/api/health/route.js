import { NextResponse } from "next/server";
import { isPostgres, sqlitePath } from "../../../lib/db";

export async function GET() {
  return NextResponse.json({
    ok: true,
    service: "onebot-web-panel",
    timestamp: new Date().toISOString(),
    database: {
      mode: isPostgres() ? "postgres" : "sqlite",
      path: isPostgres() ? null : sqlitePath(),
    },
  });
}
