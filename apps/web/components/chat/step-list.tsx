import { CircleCheck, CircleX, LoaderCircle } from "lucide-react";

import type { Step } from "@/lib/chat-state";
import { cn } from "@/lib/utils";

const ICONS = {
  started: <LoaderCircle className="size-3.5 animate-spin text-muted-foreground" aria-hidden />,
  completed: <CircleCheck className="size-3.5 text-emerald-600" aria-hidden />,
  failed: <CircleX className="size-3.5 text-destructive" aria-hidden />,
} as const;

export function StepList({ steps }: { steps: Step[] }) {
  if (steps.length === 0) return null;
  return (
    <ol className="mb-3 space-y-1.5" aria-label="Agent steps">
      {steps.map((step) => (
        <li
          key={step.id}
          className={cn(
            "flex items-center gap-2 text-xs",
            step.status === "started" ? "text-foreground" : "text-muted-foreground",
          )}
        >
          {ICONS[step.status]}
          <span>{step.status === "failed" ? `${step.label} failed` : step.label}</span>
          {step.durationMs !== null && (
            <span className="ml-auto font-mono tabular-nums">{Math.round(step.durationMs)} ms</span>
          )}
        </li>
      ))}
    </ol>
  );
}
