import { describe, expect, it } from "vitest";

import { applyEvent, newAssistantTurn } from "./chat-state";
import type { AgentEvent } from "./generated/agent-events";

const step = (status: "started" | "completed" | "failed", duration_ms: number | null = null): AgentEvent => ({
  type: "step",
  id: "s1",
  tool: "verify_customer",
  label: "Verifying…",
  status,
  duration_ms,
  detail: null,
});

describe("applyEvent", () => {
  it("appends tokens", () => {
    let turn = newAssistantTurn("a1");
    turn = applyEvent(turn, { type: "token", text: "Hello " });
    turn = applyEvent(turn, { type: "token", text: "there" });
    expect(turn.text).toBe("Hello there");
  });

  it("updates a step in place when it completes", () => {
    let turn = newAssistantTurn("a1");
    turn = applyEvent(turn, step("started"));
    turn = applyEvent(turn, step("completed", 120));
    expect(turn.steps).toEqual([
      { id: "s1", tool: "verify_customer", label: "Verifying…", status: "completed", durationMs: 120 },
    ]);
  });

  it("keeps the error status after done", () => {
    let turn = newAssistantTurn("a1");
    turn = applyEvent(turn, { type: "error", code: "llm_timeout", message: "Timed out", retryable: true });
    turn = applyEvent(turn, {
      type: "done",
      turn_id: "t1",
      usage: { input_tokens: 0, output_tokens: 0, cached_tokens: 0 },
      cost_usd: 0,
      latency_ms: 10,
    });
    expect(turn.status).toBe("error");
    expect(turn.error).toEqual({ message: "Timed out", retryable: true });
  });
});
