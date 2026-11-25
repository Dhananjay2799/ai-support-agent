import type { AssistantTurn, Turn } from "@/lib/chat-state";
import type { Span, VoiceEndedEvent } from "@/lib/generated/agent-events";

export interface SpanRow {
  span: Span;
  depth: number;
}

type Attributes = Record<string, unknown>;

const num = (value: unknown): number | undefined => (typeof value === "number" ? value : undefined);
const str = (value: unknown): string | undefined => (typeof value === "string" ? value : undefined);

export function formatCost(usd: number): string {
  if (usd === 0) return "$0";
  return usd < 0.01 ? `$${usd.toFixed(5)}` : `$${usd.toFixed(3)}`;
}

export function formatMs(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`;
}

/** Spans in tree order (each parent followed by its children), with their depth. */
export function spanTree(spans: Span[]): SpanRow[] {
  const children = new Map<string | null, Span[]>();
  const ids = new Set(spans.map((s) => s.id));
  for (const span of spans) {
    const parent = span.parent_id && ids.has(span.parent_id) ? span.parent_id : null;
    children.set(parent, [...(children.get(parent) ?? []), span]);
  }
  const rows: SpanRow[] = [];
  const visit = (parent: string | null, depth: number) => {
    for (const span of [...(children.get(parent) ?? [])].sort((a, b) => a.start_ms - b.start_ms)) {
      rows.push({ span, depth });
      visit(span.id, depth + 1);
    }
  };
  visit(null, 0);
  return rows;
}

export function spanFailed(span: Span): boolean {
  const status = (span.attributes as Attributes).status;
  return status === "failed" || status === "error";
}

/** A one-line description of what a span did. */
export function spanSummary(span: Span): string {
  const a = span.attributes as Attributes;
  switch (span.kind) {
    case "llm": {
      if (spanFailed(span)) {
        return [`attempt ${num(a.attempt) ?? 1} ${str(a.error) ?? "failed"}`, str(a.recovery) && `→ ${a.recovery}`]
          .filter(Boolean)
          .join(" ");
      }
      const tokens =
        num(a.input_tokens) !== undefined ? `${num(a.input_tokens)} → ${num(a.output_tokens) ?? 0} tok` : undefined;
      return [
        str(a.model),
        a.fallback ? "fallback" : undefined,
        tokens,
        num(a.cost_usd) !== undefined && formatCost(num(a.cost_usd)!),
      ]
        .filter(Boolean)
        .join(" · ");
    }
    case "tool":
      return [str(a.status), str(a.detail), num(a.attempts)! > 1 ? `${a.attempts} attempts` : undefined]
        .filter(Boolean)
        .join(" · ");
    case "retrieval": {
      const chunks = Array.isArray(a.chunks) ? a.chunks.length : 0;
      const best = num(a.best_cosine);
      return [best !== undefined && `best match ${best.toFixed(2)}`, `${chunks} passages`].filter(Boolean).join(" · ");
    }
    case "guardrail":
      if (Array.isArray(a.dropped)) return `dropped ${a.dropped.join(", ")}`;
      return [
        str(a.decision),
        str(a.band) && `${a.band} band`,
        num(a.confidence) !== undefined && `confidence ${a.confidence}`,
      ]
        .filter(Boolean)
        .join(" · ");
    case "voice":
      return str(a.utterance) ? `"${a.utterance}"` : "";
    default:
      if (span.name === "turn") {
        const calls = num(a.model_calls);
        return [
          str(a.channel),
          a.simulated ? `simulated ${a.simulated}` : undefined,
          calls !== undefined && `${calls} model calls`,
          num(a.cost_usd) !== undefined && formatCost(num(a.cost_usd)!),
        ]
          .filter(Boolean)
          .join(" · ");
      }
      return [str(a.reason), str(a.detail)].filter(Boolean).join(" · ");
  }
}

export interface SessionTotals {
  turns: number;
  costUsd: number;
  agentCostUsd: number;
  voiceCostUsd: number;
  voiceSeconds: number;
  inputTokens: number;
  cachedTokens: number;
  outputTokens: number;
  modelCalls: number;
  avgLatencyMs: number;
  maxLatencyMs: number;
  recoveries: number;
}

export function finishedTurns(turns: Turn[]): AssistantTurn[] {
  return turns.filter((t): t is AssistantTurn => t.role === "assistant" && t.done !== undefined);
}

export function sessionTotals(turns: Turn[], calls: VoiceEndedEvent[]): SessionTotals {
  const done = finishedTurns(turns);
  const latencies = done.map((t) => t.done!.latency_ms);
  const agentCost = done.reduce((sum, t) => sum + t.done!.cost_usd, 0);
  const voiceCost = calls.reduce((sum, c) => sum + (c.voice_cost_usd ?? 0), 0);
  const spans = done.flatMap((t) => t.trace?.spans ?? []);
  return {
    turns: done.length,
    agentCostUsd: agentCost,
    voiceCostUsd: voiceCost,
    costUsd: agentCost + voiceCost,
    voiceSeconds: calls.reduce((sum, c) => sum + (c.billed_seconds ?? c.duration_ms / 1000), 0),
    inputTokens: done.reduce((sum, t) => sum + t.done!.usage.input_tokens, 0),
    cachedTokens: done.reduce((sum, t) => sum + t.done!.usage.cached_tokens, 0),
    outputTokens: done.reduce((sum, t) => sum + t.done!.usage.output_tokens, 0),
    modelCalls: spans.filter((s) => s.kind === "llm" && !spanFailed(s)).length,
    avgLatencyMs: latencies.length ? latencies.reduce((a, b) => a + b, 0) / latencies.length : 0,
    maxLatencyMs: latencies.length ? Math.max(...latencies) : 0,
    recoveries:
      spans.filter(
        (s) =>
          spanFailed(s) &&
          (s.attributes as Attributes).recovery !== undefined &&
          (s.attributes as Attributes).recovery !== "give up",
      ).length +
      spans.filter((s) => s.kind === "tool" && num((s.attributes as Attributes).attempts)! > 1 && !spanFailed(s))
        .length,
  };
}
