import { API_URL } from "@/lib/chat-client";
import { errorDetail } from "@/lib/http";

/** Restore the demo customers, charges, refunds and tickets (demo mode only). */
export async function resetDemoData(): Promise<void> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}/demo/reset`, { method: "POST" });
  } catch {
    throw new Error("Can't reach the support service.");
  }
  if (!response.ok) throw new Error((await errorDetail(response)) ?? `Reset failed (${response.status}).`);
}
