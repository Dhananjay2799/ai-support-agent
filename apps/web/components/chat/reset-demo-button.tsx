"use client";

import { Check, LoaderCircle, RotateCcw } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { resetDemoData } from "@/lib/demo";

type State = "idle" | "confirm" | "resetting" | "done" | "failed";

const LABELS: Record<State, string> = {
  idle: "Reset demo",
  confirm: "Reset demo data?",
  resetting: "Resetting…",
  done: "Demo reset",
  failed: "Reset failed",
};

/** Two-step reset of the sample data, e.g. right before recording a demo. */
export function ResetDemoButton({ onReset }: { onReset: () => void }) {
  const [state, setState] = useState<State>("idle");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (state === "idle" || state === "resetting") return;
    const back = setTimeout(() => setState("idle"), state === "confirm" ? 4_000 : 2_500);
    return () => clearTimeout(back);
  }, [state]);

  const onClick = async () => {
    if (state !== "confirm") {
      setState("confirm");
      return;
    }
    setState("resetting");
    try {
      await resetDemoData();
      setError(null);
      setState("done");
      onReset();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Reset failed.");
      setState("failed");
    }
  };

  const Icon = state === "resetting" ? LoaderCircle : state === "done" ? Check : RotateCcw;
  return (
    <Button
      variant={state === "confirm" ? "destructive" : "outline"}
      size="sm"
      disabled={state === "resetting"}
      onClick={() => void onClick()}
      title={error ?? "Restore the demo customers, charges, refunds and tickets"}
      aria-live="polite"
    >
      <Icon data-icon="inline-start" className={state === "resetting" ? "animate-spin" : undefined} />
      <span className={state === "idle" ? "hidden sm:inline" : undefined}>{LABELS[state]}</span>
      {state === "idle" && <span className="sr-only sm:hidden">{LABELS.idle}</span>}
    </Button>
  );
}
