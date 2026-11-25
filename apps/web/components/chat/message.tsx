import { Activity, Mic, RotateCcw, ShieldCheck, TriangleAlert } from "lucide-react";

import { Answer } from "@/components/chat/answer";
import { EscalationCard } from "@/components/chat/escalation-card";
import { StepList } from "@/components/chat/step-list";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { Turn } from "@/lib/chat-state";

function confidenceLabel(confidence: number): string {
  if (confidence >= 0.75) return "High confidence";
  if (confidence >= 0.5) return "Medium confidence";
  return "Low confidence";
}

export function Message({
  turn,
  onRetry,
  onShowTrace,
}: {
  turn: Turn;
  onRetry?: () => void;
  onShowTrace?: () => void;
}) {
  if (turn.role === "user") {
    return (
      <div className="flex justify-end">
        <p className="max-w-[80%] rounded-2xl rounded-br-sm bg-primary px-4 py-2.5 text-sm whitespace-pre-wrap text-primary-foreground">
          {turn.via === "voice" && <Mic className="mr-1.5 -mt-0.5 inline size-3.5 opacity-70" aria-label="Spoken" />}
          {turn.text}
        </p>
      </div>
    );
  }

  const streaming = turn.status === "streaming";
  const waiting = streaming && turn.text === "" && turn.steps.length === 0;
  const grounded = turn.guardrail?.decision === "answer" && !turn.escalation;

  return (
    <div className="max-w-full rounded-2xl rounded-bl-sm sm:max-w-[85%] border bg-card px-4 py-3 text-sm shadow-xs">
      {turn.via === "voice" && (
        <p className="mb-2 flex items-center gap-1 text-[11px] font-medium text-muted-foreground">
          <Mic className="size-3" aria-hidden /> Answered by voice · the agent said this in its own words
        </p>
      )}
      <StepList steps={turn.steps} />
      {waiting && <p className="text-muted-foreground">Thinking…</p>}
      {turn.text && <Answer text={turn.text} citations={turn.citations} streaming={streaming} />}
      {turn.escalation && <EscalationCard escalation={turn.escalation} />}

      {turn.error && (
        <div className="mt-3 flex items-center gap-2 rounded-lg bg-destructive/10 px-3 py-2 text-destructive">
          <TriangleAlert className="size-4 shrink-0" aria-hidden />
          <span className="text-xs">{turn.error.message}</span>
          {turn.error.retryable && onRetry && (
            <Button size="xs" variant="outline" className="ml-auto" onClick={onRetry}>
              <RotateCcw data-icon="inline-start" />
              Retry
            </Button>
          )}
        </div>
      )}

      {turn.done && (
        <div className="mt-3 flex items-center gap-2">
          {grounded && turn.guardrail && (
            <Badge variant="secondary" title={turn.guardrail.reason ?? undefined}>
              <ShieldCheck data-icon="inline-start" />
              {confidenceLabel(turn.guardrail.confidence)}
            </Badge>
          )}
          <button
            type="button"
            onClick={onShowTrace}
            disabled={!onShowTrace}
            title="Show this turn's trace"
            className="ml-auto flex items-center gap-1 rounded font-mono text-[11px] text-muted-foreground tabular-nums hover:text-foreground disabled:hover:text-muted-foreground"
          >
            {(turn.done.latency_ms / 1000).toFixed(1)} s · ${turn.done.cost_usd.toFixed(4)}
            {onShowTrace && <Activity className="size-3" aria-hidden />}
          </button>
        </div>
      )}
    </div>
  );
}
