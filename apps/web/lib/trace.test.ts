import { describe, expect, it } from "vitest";

import { type AssistantTurn, newAssistantTurn } from "./chat-state";
import type { Span } from "./generated/agent-events";
import { sessionTotals, spanSummary, spanTree } from "./trace";

const span = (id: string, parent: string | null, start: number, extra: Partial<Span> = {}): Span => ({
  id,
  parent_id: parent,
  name: id,
  kind: "node",
  start_ms: start,
  end_ms: start + 10,
  attributes: {},
  ...extra,
});

describe("spanTree", () => {
  it("orders spans parent-first by start time, with depth", () => {
    const rows = spanTree([
      span("root", null, 0),
      span("tool", "root", 50),
      span("search", "tool", 55),
      span("llm", "root", 5),
    ]);
    expect(rows.map((r) => `${r.depth}:${r.span.id}`)).toEqual(["0:root", "1:llm", "1:tool", "2:search"]);
  });
});

describe("spanSummary", () => {
  it("explains a failed model call and what happened next", () => {
    const failed = span("a", null, 0, {
      kind: "llm",
      attributes: { attempt: 2, status: "failed", error: "rate limited (429)", recovery: "fallback model" },
    });
    expect(spanSummary(failed)).toBe("attempt 2 rate limited (429) → fallback model");
  });

  it("shows model, tokens and cost for a successful call", () => {
    const ok = span("b", null, 0, {
      kind: "llm",
      attributes: { model: "gpt-5.4-mini", fallback: true, input_tokens: 1038, output_tokens: 159, cost_usd: 0.0015 },
    });
    expect(spanSummary(ok)).toBe("gpt-5.4-mini · fallback · 1038 → 159 tok · $0.00150");
  });
});

describe("turn summary", () => {
  it("names the channel, calls and cost", () => {
    const root = span("root", null, 0, { attributes: { channel: "voice", model_calls: 4, cost_usd: 0.0004 } });
    expect(spanSummary({ ...root, name: "turn" })).toBe("voice · 4 model calls · $0.00040");
  });
});

describe("sessionTotals", () => {
  it("adds agent turns and voice calls, and counts recoveries", () => {
    const turn: AssistantTurn = {
      ...newAssistantTurn("t1"),
      status: "done",
      done: {
        type: "done",
        turn_id: "t1",
        usage: { input_tokens: 1000, cached_tokens: 200, output_tokens: 50 },
        cost_usd: 0.0004,
        latency_ms: 5000,
      },
      trace: {
        type: "trace",
        turn_id: "t1",
        spans: [
          span("m1", null, 0, { kind: "llm", attributes: { status: "failed", recovery: "retry" } }),
          span("m2", null, 1, { kind: "llm", attributes: { status: "ok" } }),
          span("t", null, 2, { kind: "tool", attributes: { status: "completed", attempts: 2 } }),
        ],
      },
    };
    const totals = sessionTotals(
      [turn],
      [{ type: "voice_ended", reason: "hangup", duration_ms: 60_000, billed_seconds: 60, voice_cost_usd: 0.05 }],
    );
    expect(totals.turns).toBe(1);
    expect(totals.costUsd).toBeCloseTo(0.0504);
    expect(totals.voiceSeconds).toBe(60);
    expect(totals.modelCalls).toBe(1);
    expect(totals.recoveries).toBe(2);
  });
});
