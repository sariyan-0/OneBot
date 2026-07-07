import { redirectSeeOther } from "../../../../lib/redirect";
import { restartXray } from "../../../../lib/xui";

export async function POST(request) {
  await restartXray();
  return redirectSeeOther(request, "/admin/server");
}
