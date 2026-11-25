"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import type { AgentEvent, VoiceEndedEvent } from "@/lib/generated/agent-events";
import { type Call, CallError, startCall } from "@/lib/voice-client";
import { applyLiveEvent, type CallState, INITIAL_CALL } from "@/lib/voice-state";

export interface VoiceCallOptions {
  sessionId: () => string;
  personaId: string | null;
  /** The caller asked for something; the agent's events for it follow. */
  onVoiceTurn: (utterance: string) => void;
  /** An event of the current voice turn. Returns false if no turn is in progress. */
  onAgentEvent: (event: AgentEvent) => boolean;
  /** The call's stream is over; a voice turn still in progress won't get more events. */
  onCallClosed: () => void;
  /** The call ended, with its duration and voice cost. */
  onCallEnded?: (event: VoiceEndedEvent) => void;
}

export interface VoiceCall {
  state: CallState | null;
  mic: MediaStream | null;
  agent: MediaStream | null;
  start: () => Promise<void>;
  hangUp: () => Promise<void>;
  setMuted: (muted: boolean) => void;
  dismiss: () => void;
}

export function useVoiceCall({
  sessionId,
  personaId,
  onVoiceTurn,
  onAgentEvent,
  onCallClosed,
  onCallEnded,
}: VoiceCallOptions): VoiceCall {
  const [state, setState] = useState<CallState | null>(null);
  const [mic, setMic] = useState<MediaStream | null>(null);
  const [agent, setAgent] = useState<MediaStream | null>(null);
  const callRef = useRef<Call | null>(null);
  const handlers = useRef({ onVoiceTurn, onAgentEvent, onCallClosed, onCallEnded });
  useEffect(() => {
    handlers.current = { onVoiceTurn, onAgentEvent, onCallClosed, onCallEnded };
  }, [onVoiceTurn, onAgentEvent, onCallClosed, onCallEnded]);

  const update = useCallback((change: (state: CallState) => CallState) => {
    setState((current) => (current ? change(current) : current));
  }, []);

  const fail = useCallback(
    (message: string) => update((s) => (s.status === "ended" ? s : { ...s, status: "failed", error: message })),
    [update],
  );

  const start = useCallback(async () => {
    setState(INITIAL_CALL);
    setAgent(null);
    try {
      const call = await startCall(
        { session_id: sessionId(), persona_id: personaId },
        {
          onLiveEvent: (event) => update((s) => applyLiveEvent(s, event)),
          onFailure: (error) => fail(error.message),
          onClosed: () => handlers.current.onCallClosed(),
          onVoiceEvent: (event) => {
            switch (event.type) {
              case "voice_session":
                update((s) => ({ ...s, maxSeconds: event.max_seconds }));
                break;
              case "voice_turn":
                handlers.current.onVoiceTurn(event.utterance);
                break;
              case "voice_ended":
                handlers.current.onCallEnded?.(event);
                update((s) => ({
                  ...s,
                  status: s.status === "failed" ? "failed" : "ended",
                  endReason: event.reason,
                }));
                break;
              default:
                if (!handlers.current.onAgentEvent(event) && event.type === "error") fail(event.message);
            }
          },
        },
      );
      callRef.current = call;
      setMic(call.mic);
      void call.agent.then(setAgent);
    } catch (error) {
      setState({
        ...INITIAL_CALL,
        status: "failed",
        error: error instanceof CallError ? error.message : "Couldn't start the call.",
      });
    }
  }, [sessionId, personaId, update, fail]);

  const hangUp = useCallback(async () => {
    const call = callRef.current;
    callRef.current = null;
    update((s) => (s.status === "failed" ? s : { ...s, status: "ended", endReason: s.endReason ?? "hangup" }));
    await call?.hangUp();
  }, [update]);

  const setMuted = useCallback((muted: boolean) => callRef.current?.setMuted(muted), []);

  const dismiss = useCallback(() => {
    setState(null);
    setMic(null);
    setAgent(null);
  }, []);

  // Leaving the page or switching conversation hangs up.
  useEffect(() => () => void callRef.current?.hangUp(), []);

  return { state, mic, agent, start, hangUp, setMuted, dismiss };
}
