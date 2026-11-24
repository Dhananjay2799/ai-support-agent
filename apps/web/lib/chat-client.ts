import type { AgentEvent, ChatRequest } from "@/lib/generated/agent-events";
import { SSEParser } from "@/lib/sse";

export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

const EVENT_TYPES = new Set<AgentEvent["type"]>([
  "token",
  "step",
  "guardrail",
  "escalation",
  "citations",
  "trace",
  "error",
  "done",
]);

export class ChatStreamError extends Error {
  constructor(
    message: string,
    readonly kind: "http" | "rate_limited" | "interrupted" | "network",
    readonly retryAfterSeconds?: number,
  ) {
    super(message);
    this.name = "ChatStreamError";
  }
}

function isAgentEvent(value: unknown): value is AgentEvent {
  return (
    typeof value === "object" &&
    value !== null &&
    "type" in value &&
    EVENT_TYPES.has((value as { type: AgentEvent["type"] }).type)
  );
}

/**
 * POST one chat turn and invoke `onEvent` for every streamed AgentEvent.
 * Resolves after the `done` event; rejects with ChatStreamError otherwise.
 */
export async function streamChat(
  request: ChatRequest,
  { onEvent, signal }: { onEvent: (event: AgentEvent) => void; signal?: AbortSignal },
): Promise<void> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify(request),
      signal,
    });
  } catch (error) {
    if (signal?.aborted) throw error;
    throw new ChatStreamError("Can't reach the support service.", "network");
  }

  if (response.status === 429) {
    const retryAfter = Number(response.headers.get("Retry-After") ?? "0") || undefined;
    throw new ChatStreamError("Too many messages. Please wait a moment.", "rate_limited", retryAfter);
  }
  if (!response.ok || !response.body) {
    throw new ChatStreamError(`The support service returned ${response.status}.`, "http");
  }

  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  const parser = new SSEParser();
  let finished = false;

  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    for (const message of parser.feed(value)) {
      const payload: unknown = JSON.parse(message.data);
      if (!isAgentEvent(payload)) continue; // forward-compatible: ignore unknown events
      onEvent(payload);
      if (payload.type === "done") finished = true;
    }
  }

  if (!finished) {
    throw new ChatStreamError("The connection dropped before the reply finished.", "interrupted");
  }
}
