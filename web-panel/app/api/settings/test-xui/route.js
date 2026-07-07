import { NextResponse } from "next/server";
import { getServerStatus } from "../../../../lib/xui";

export async function POST() {
  try {
    const status = await getServerStatus();
    return NextResponse.json({
      ok: true,
      message: `Connected to ${status?.panelVersion || "3X-UI"} successfully.`,
      status,
    });
  } catch (error) {
    return NextResponse.json(
      {
        ok: false,
        error: error instanceof Error ? error.message : "Unable to connect to 3X-UI",
      },
      { status: 400 }
    );
  }
}
