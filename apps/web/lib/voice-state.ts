import type { VoiceEndedEvent } from "@/lib/generated/agent-events";

/** The GPT-Live data-channel events the browser is allowed to receive (see BROWSER_CHANNEL). */
export type LiveEvent =
  | {
      type: "session.input_transcript.delta" | "session.output_transcript.delta";
      delta: string;
      start_ms: number;
      end_ms: number;
    }
  | { type: "session.started" | "session.closed" | "session.paused" | "session.resumed" }
  | { type: "session.input_audio.muted" | "session.input_audio.unmuted" }
  | { type: "error"; error?: { message?: string } };

export type CallStatus = "connecting" | "live" | "ended" | "failed";

export interface Caption {
  id: number;
  speaker: "caller" | "agent";
  text: string;
  endMs: number;
  /** The caller talked over this agent line, and the agent stopped. */
  interrupted: boolean;
}

export interface CallState {
  status: CallStatus;
  captions: Caption[];
  muted: boolean;
  maxSeconds: number | null;
  connectedAt: number | null;
  endReason?: VoiceEndedEvent["reason"];
  error?: string;
}

export const INITIAL_CALL: CallState = {
  status: "connecting",
  captions: [],
  muted: false,
  maxSeconds: null,
  connectedAt: null,
};

/** A pause longer than this starts a new caption line for the same speaker. */
const SAME_LINE_GAP_MS = 1_500;
/**
 * Caller speech this soon after an agent line that stopped mid-sentence marks a barge-in.
 * Transcript times are when audio was generated, not played, so the caller's first words
 * can land a second after the agent's last ones even when they talked over it (see
 * __fixtures__/voice-interruption.json, recorded from a real call).
 */
const BARGE_IN_MS = 2_000;
const SENTENCE_END = /[.?!…]["')\]]?\s*$/;
const MAX_CAPTIONS = 40;

function addTranscript(
  captions: Caption[],
  speaker: Caption["speaker"],
  delta: string,
  startMs: number,
  endMs: number,
): Caption[] {
  let next = captions;
  const last = next.at(-1);

  // GPT-Live stops talking by itself when the caller cuts in; this only shows it.
  if (
    speaker === "caller" &&
    last?.speaker === "agent" &&
    startMs < last.endMs + BARGE_IN_MS &&
    !SENTENCE_END.test(last.text)
  ) {
    next = [...next.slice(0, -1), { ...last, interrupted: true }];
  }

  const current = next.at(-1);
  if (current?.speaker === speaker && startMs - current.endMs < SAME_LINE_GAP_MS) {
    return [...next.slice(0, -1), { ...current, text: current.text + delta, endMs: Math.max(current.endMs, endMs) }];
  }
  const id = (current?.id ?? 0) + 1;
  const text = delta.replace(/^[\s,;:]+/, ""); // a new line shouldn't start with ", I think"
  return [...next, { id, speaker, text, endMs, interrupted: false }].slice(-MAX_CAPTIONS);
}

export function applyLiveEvent(state: CallState, event: LiveEvent): CallState {
  switch (event.type) {
    case "session.started":
      return { ...state, status: "live", connectedAt: state.connectedAt ?? Date.now() };
    case "session.input_transcript.delta":
      return { ...state, captions: addTranscript(state.captions, "caller", event.delta, event.start_ms, event.end_ms) };
    case "session.output_transcript.delta":
      return { ...state, captions: addTranscript(state.captions, "agent", event.delta, event.start_ms, event.end_ms) };
    case "session.input_audio.muted":
      return { ...state, muted: true };
    case "session.input_audio.unmuted":
      return { ...state, muted: false };
    case "session.closed":
      return state.status === "failed" ? state : { ...state, status: "ended" };
    default:
      return state;
  }
}
