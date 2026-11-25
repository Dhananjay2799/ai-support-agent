"use client";

import { Mic, MicOff, PhoneOff, RotateCcw, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import type { VoiceCall } from "@/components/voice/use-voice-call";
import { type Speaker, Waveform } from "@/components/voice/waveform";
import { cn } from "@/lib/utils";

const END_REASONS = {
  hangup: "Call ended",
  closed: "Call ended",
  time_limit: "Call ended at the demo's 3-minute limit",
  error: "The call ended unexpectedly",
} as const;

function clock(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function useSecondsLeft(connectedAt: number | null, maxSeconds: number | null, running: boolean): number | null {
  // The clock is only read in timer callbacks, never while rendering (or prerendering).
  const [now, setNow] = useState<number | null>(null);
  useEffect(() => {
    if (!running) return;
    const tick = () => setNow(Date.now());
    const first = setTimeout(tick, 0);
    const timer = setInterval(tick, 1_000);
    return () => {
      clearTimeout(first);
      clearInterval(timer);
    };
  }, [running]);
  if (connectedAt === null || maxSeconds === null || now === null) return null;
  return maxSeconds - Math.max(0, now - connectedAt) / 1000;
}

export function CallPanel({ call, onRetry }: { call: VoiceCall; onRetry: () => void }) {
  const { state, mic, agent } = call;
  const audioRef = useRef<HTMLAudioElement>(null);
  const [speaker, setSpeaker] = useState<Speaker>(null);
  const live = state?.status === "live";
  const secondsLeft = useSecondsLeft(state?.connectedAt ?? null, state?.maxSeconds ?? null, live);

  useEffect(() => {
    if (audioRef.current) audioRef.current.srcObject = agent;
  }, [agent]);

  if (!state) return null;
  const active = state.status === "connecting" || state.status === "live";
  const captions = state.captions.slice(-3);

  const status =
    state.status === "connecting"
      ? "Connecting…"
      : state.status === "failed"
        ? (state.error ?? "The call failed.")
        : state.status === "ended"
          ? END_REASONS[state.endReason ?? "hangup"]
          : state.muted
            ? "Muted"
            : speaker === "agent"
              ? "Agent speaking"
              : speaker === "caller"
                ? "Listening to you"
                : "Listening…";

  return (
    <section aria-label="Voice call" className="border-t bg-muted/30">
      <audio ref={audioRef} autoPlay />
      <div className="mx-auto max-w-3xl space-y-2 px-4 py-3">
        <div className="flex items-center gap-2 text-xs">
          <span
            className={cn(
              "size-2 rounded-full",
              state.status === "live"
                ? "animate-pulse bg-emerald-500"
                : state.status === "failed"
                  ? "bg-destructive"
                  : "bg-muted-foreground/50",
            )}
            aria-hidden
          />
          <span className={cn("font-medium", state.status === "failed" && "text-destructive")} role="status">
            {status}
          </span>
          {live && secondsLeft !== null && (
            <span
              className={cn(
                "ml-auto font-mono tabular-nums text-muted-foreground",
                secondsLeft < 30 && "text-amber-600",
              )}
              title="Demo calls are capped at 3 minutes"
            >
              {clock(secondsLeft)} left
            </span>
          )}
        </div>

        {active && <Waveform mic={mic} agent={agent} onSpeaker={setSpeaker} />}

        {captions.length > 0 && (
          <ol className="space-y-1 text-sm" aria-label="Live captions" aria-live="polite">
            {captions.map((caption) => (
              <li
                key={caption.id}
                className={cn(
                  caption.speaker === "caller" ? "text-emerald-700 dark:text-emerald-400" : "text-foreground",
                )}
              >
                <span className="mr-1.5 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
                  {caption.speaker === "caller" ? "You" : "Agent"}
                </span>
                {caption.text}
                {caption.interrupted && (
                  <span className="ml-1.5 rounded bg-amber-500/15 px-1 py-px text-[10px] font-medium text-amber-700 dark:text-amber-400">
                    interrupted
                  </span>
                )}
              </li>
            ))}
          </ol>
        )}

        <div className="flex items-center gap-2 pt-1">
          {active ? (
            <>
              <Button
                variant="outline"
                size="sm"
                disabled={!live}
                aria-pressed={state.muted}
                onClick={() => call.setMuted(!state.muted)}
              >
                {state.muted ? <MicOff data-icon="inline-start" /> : <Mic data-icon="inline-start" />}
                {state.muted ? "Unmute" : "Mute"}
              </Button>
              <Button variant="destructive" size="sm" className="ml-auto" onClick={() => void call.hangUp()}>
                <PhoneOff data-icon="inline-start" />
                Hang up
              </Button>
            </>
          ) : (
            <>
              <Button variant="outline" size="sm" onClick={onRetry}>
                <RotateCcw data-icon="inline-start" />
                {state.status === "failed" ? "Try again" : "Call again"}
              </Button>
              <Button variant="ghost" size="sm" className="ml-auto" onClick={call.dismiss}>
                <X data-icon="inline-start" />
                Close
              </Button>
            </>
          )}
        </div>
      </div>
    </section>
  );
}
