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
      {steps.map((step) => {
        const label = step.status === "started" ? step.label : step.label.replace(/…$/, "");
        return (
          <li
            key={step.id}
            className={cn(
              "flex items-start gap-2 text-xs",
              step.status === "started" ? "text-foreground" : "text-muted-foreground",
            )}
          >
            <span className="mt-px">{ICONS[step.status]}</span>
            <span className="min-w-0">
              {step.status === "failed" ? `${label} failed` : label}
              {step.detail && step.status !== "started" && <span className="text-foreground/80"> · {step.detail}</span>}
            </span>
            {step.durationMs !== null && (
              <span className="ml-auto shrink-0 pl-2 font-mono tabular-nums">{Math.round(step.durationMs)} ms</span>
            )}
          </li>
        );
      })}
    </ol>
  );
}
