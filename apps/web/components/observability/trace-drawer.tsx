"use client";

import { Activity, Mic, MessageSquare, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { SpanDetails } from "@/components/observability/span-details";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { AssistantTurn, Turn } from "@/lib/chat-state";
import type { ChatRequest, Span, VoiceEndedEvent } from "@/lib/generated/agent-events";
import { finishedTurns, formatCost, formatMs, sessionTotals, spanFailed, spanSummary, spanTree } from "@/lib/trace";
import { cn } from "@/lib/utils";

export type Simulation = NonNullable<ChatRequest["simulate"]>;

const SIMULATIONS: { value: Simulation; label: string }[] = [
  { value: "llm_timeout", label: "Model timeout → retry" },
  { value: "rate_limit", label: "Rate limit → fallback model" },
  { value: "tool_error", label: "Database blip → tool retried" },
  { value: "tool_outage", label: "Account system outage → graceful" },
];

const KIND_COLORS: Record<Span["kind"], string> = {
  node: "bg-zinc-400",
  llm: "bg-violet-500",
  tool: "bg-sky-500",
  retrieval: "bg-amber-500",
  guardrail: "bg-emerald-500",
  voice: "bg-pink-500",
};

const KIND_LABELS: Record<Span["kind"], string> = {
  node: "turn",
  llm: "model",
  tool: "tool",
  retrieval: "search",
  guardrail: "check",
  voice: "voice",
};

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg border bg-card px-2.5 py-2" title={hint}>
      <div className="text-[10px] tracking-wide text-muted-foreground uppercase">{label}</div>
      <div className="mt-0.5 font-mono text-sm tabular-nums">{value}</div>
    </div>
  );
}

function Waterfall({ spans }: { spans: Span[] }) {
  const [open, setOpen] = useState<string | null>(null);
  const rows = useMemo(() => spanTree(spans), [spans]);
  const total = Math.max(1, ...spans.map((s) => s.end_ms ?? s.start_ms));

  return (
    <ol className="space-y-px" aria-label="Spans">
      {rows.map(({ span, depth }) => {
        const failed = spanFailed(span);
        const left = (span.start_ms / total) * 100;
        const width = Math.max(0.8, (((span.end_ms ?? span.start_ms) - span.start_ms) / total) * 100);
        return (
          <li key={span.id}>
            <button
              type="button"
              onClick={() => setOpen((current) => (current === span.id ? null : span.id))}
              aria-expanded={open === span.id}
              className="grid w-full grid-cols-[minmax(0,1fr)_7rem] items-center gap-2 rounded px-1 py-1 text-left hover:bg-muted/60"
            >
              <span className="min-w-0" style={{ paddingLeft: `${depth * 10}px` }}>
                <span className="flex items-center gap-1.5 text-xs">
                  <span
                    className={cn("size-1.5 shrink-0 rounded-full", failed ? "bg-destructive" : KIND_COLORS[span.kind])}
                  />
                  <span className={cn("truncate font-medium", failed && "text-destructive")}>{span.name}</span>
                  <span className="shrink-0 text-[10px] text-muted-foreground">{KIND_LABELS[span.kind]}</span>
                </span>
                <span className="block truncate pl-3 text-[11px] text-muted-foreground">{spanSummary(span)}</span>
              </span>
              <span
                className="relative h-3 rounded-sm bg-muted/50"
                title={formatMs((span.end_ms ?? span.start_ms) - span.start_ms)}
              >
                <span
                  className={cn("absolute inset-y-0 rounded-sm", failed ? "bg-destructive" : KIND_COLORS[span.kind])}
                  style={{ left: `${Math.min(left, 99)}%`, width: `${Math.min(width, 100 - Math.min(left, 99))}%` }}
                />
              </span>
            </button>
            {open === span.id && <SpanDetails span={span} />}
          </li>
        );
      })}
    </ol>
  );
}

function TurnRow({
  turn,
  index,
  question,
  selected,
  onSelect,
}: {
  turn: AssistantTurn;
  index: number;
  question: string;
  selected: boolean;
  onSelect: () => void;
}) {
  const spans = turn.trace?.spans ?? [];
  const failures = spans.filter(spanFailed).length;
  const simulated = (spans[0]?.attributes as Record<string, unknown> | undefined)?.simulated;
  return (
    <li className={cn("rounded-lg border", selected && "border-primary/40 bg-muted/20")}>
      <button
        type="button"
        onClick={onSelect}
        aria-expanded={selected}
        className="flex w-full items-start gap-2 p-2.5 text-left"
      >
        <span className="mt-0.5 font-mono text-[10px] text-muted-foreground">{index}</span>
        {turn.via === "voice" ? (
          <Mic className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" aria-label="Voice" />
        ) : (
          <MessageSquare className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" aria-label="Text" />
        )}
        <span className="min-w-0 flex-1">
          <span className="line-clamp-2 text-xs">{question}</span>
          <span className="mt-1 flex flex-wrap items-center gap-1.5 font-mono text-[10.5px] text-muted-foreground tabular-nums">
            {formatMs(turn.done!.latency_ms)} · {formatCost(turn.done!.cost_usd)} ·{" "}
            {turn.done!.usage.input_tokens + turn.done!.usage.output_tokens} tok
            {failures > 0 && (
              <Badge
                variant="outline"
                className="h-4 border-amber-500/40 px-1 text-[10px] text-amber-700 dark:text-amber-400"
              >
                {failures} failed {failures === 1 ? "step" : "steps"}, handled
              </Badge>
            )}
            {typeof simulated === "string" && (
              <Badge variant="outline" className="h-4 px-1 text-[10px]">
                simulated
              </Badge>
            )}
          </span>
        </span>
      </button>
      {selected && (
        <div className="border-t px-1.5 py-2">
          {spans.length > 0 ? (
            <Waterfall spans={spans} />
          ) : (
            <p className="px-1 text-xs text-muted-foreground">No trace for this turn.</p>
          )}
        </div>
      )}
    </li>
  );
}

export function TraceDrawer({
  turns,
  calls,
  selectedTurnId,
  onSelectTurn,
  simulate,
  onSimulate,
  simulateDisabled,
  onClose,
}: {
  turns: Turn[];
  calls: VoiceEndedEvent[];
  selectedTurnId: string | null;
  onSelectTurn: (id: string | null) => void;
  simulate: Simulation | null;
  onSimulate: (value: Simulation | null) => void;
  simulateDisabled: boolean;
  onClose: () => void;
}) {
  const done = finishedTurns(turns);
  const totals = sessionTotals(turns, calls);
  const questions = new Map<string, string>();
  turns.forEach((turn, i) => {
    const previous = turns[i - 1];
    if (turn.role === "assistant" && previous?.role === "user") questions.set(turn.id, previous.text);
  });

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <aside
      aria-label="Observability"
      className="fixed inset-y-0 right-0 z-40 flex w-full max-w-md flex-col border-l bg-background shadow-2xl lg:static lg:z-auto lg:w-[26rem] lg:max-w-none lg:shadow-none"
    >
      <div className="flex items-center gap-2 border-b px-4 py-3">
        <Activity className="size-4 text-muted-foreground" aria-hidden />
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-medium">Observability</h2>
          <p className="text-[11px] text-muted-foreground">Every model call, tool and check, per turn</p>
        </div>
        <Button variant="ghost" size="icon" aria-label="Close observability" onClick={onClose}>
          <X />
        </Button>
      </div>

      <div className="flex-1 space-y-4 overflow-y-auto px-4 py-3">
        <section aria-label="Session totals" className="grid grid-cols-3 gap-1.5">
          <Stat
            label="Cost"
            value={formatCost(totals.costUsd)}
            hint={`Agent ${formatCost(totals.agentCostUsd)} + voice ${formatCost(totals.voiceCostUsd)}`}
          />
          <Stat label="Turns" value={String(totals.turns)} />
          <Stat
            label="Latency"
            value={totals.turns ? formatMs(totals.avgLatencyMs) : "–"}
            hint={`Average per turn; slowest ${formatMs(totals.maxLatencyMs)}`}
          />
          <Stat
            label="Tokens"
            value={`${totals.inputTokens + totals.outputTokens}`}
            hint={`${totals.inputTokens} in (${totals.cachedTokens} cached), ${totals.outputTokens} out`}
          />
          <Stat
            label="Model calls"
            value={String(totals.modelCalls)}
            hint="Agent, judge and embedding calls that succeeded"
          />
          <Stat
            label="Recovered"
            value={String(totals.recoveries)}
            hint="Failed calls retried or rerouted to a fallback"
          />
        </section>
        {calls.length > 0 && (
          <p className="text-[11px] text-muted-foreground">
            Voice: {calls.length} {calls.length === 1 ? "call" : "calls"}, {Math.round(totals.voiceSeconds)} s billed,{" "}
            {formatCost(totals.voiceCostUsd)} (GPT-Live $0.05/min; agent turns counted separately).
          </p>
        )}

        <section aria-label="Failure simulator" className="space-y-1.5 rounded-lg border border-dashed p-2.5">
          <label className="flex items-center gap-2 text-xs">
            <span className="font-medium">Simulate a failure</span>
            <select
              value={simulate ?? ""}
              disabled={simulateDisabled}
              onChange={(event) => onSimulate((event.target.value || null) as Simulation | null)}
              className="ml-auto h-7 min-w-0 rounded-md border bg-background px-1.5 text-xs outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
              aria-label="Failure to simulate on the next message"
            >
              <option value="">None</option>
              {SIMULATIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <p className="text-[11px] text-muted-foreground">
            Demo only. Injected into the next typed message, then the trace shows how it was recovered.
          </p>
        </section>

        <section aria-label="Turns" className="space-y-1.5">
          <h3 className="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">Turns</h3>
          {done.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              Send a message or start a call; each turn&apos;s trace appears here.
            </p>
          ) : (
            <ol className="space-y-1.5">
              {done.map((turn, i) => (
                <TurnRow
                  key={turn.id}
                  turn={turn}
                  index={i + 1}
                  question={questions.get(turn.id) ?? ""}
                  selected={turn.id === selectedTurnId}
                  onSelect={() => onSelectTurn(turn.id === selectedTurnId ? null : turn.id)}
                />
              ))}
            </ol>
          )}
        </section>
      </div>
    </aside>
  );
}
