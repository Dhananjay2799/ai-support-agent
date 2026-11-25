import { describe, expect, it } from "vitest";

import timeline from "./__fixtures__/voice-interruption.json";
import { applyLiveEvent, type CallState, INITIAL_CALL, type LiveEvent } from "./voice-state";

function replay(events: LiveEvent[], from: CallState = INITIAL_CALL): CallState {
  return events.reduce(applyLiveEvent, from);
}

describe("call captions", () => {
  const state = replay([{ type: "session.started" }, ...(timeline as LiveEvent[])]);
  const lines = state.captions.map((c) => `${c.speaker}${c.interrupted ? " (cut off)" : ""}: ${c.text}`);

  it("turns a recorded call into caption lines, marking where the caller cut in", () => {
    expect(state.status).toBe("live");
    expect(lines).toEqual([
      "caller: Hi",
      "agent (cut off): Hi", // the caller carried on over the greeting
      "caller: I think I was charged twice this month for my subscription. Can you fix that please",
      "agent: Sure, uh let me pull up your account.",
      "agent (cut off): Thanks for waiting, Priya. I’ve refunded",
      "caller: Sorry to",
      "agent: Oh.",
      "caller: cut in, what does my plan include storage-wise",
      "agent: Right, let me check your storage details.",
      "agent: Your Pro plan includes 1 terabyte of storage.",
    ]);
  });

  it("tracks mute and close", () => {
    const muted = replay([{ type: "session.input_audio.muted" }], state);
    expect(muted.muted).toBe(true);
    expect(replay([{ type: "session.closed" }], muted).status).toBe("ended");
  });
});
