import { API_URL } from "@/lib/chat-client";
import type { VoiceEvent, VoiceRequest } from "@/lib/generated/agent-events";
import { SSEParser } from "@/lib/sse";
import type { LiveEvent } from "@/lib/voice-state";

export class CallError extends Error {
  constructor(
    message: string,
    readonly kind: "microphone" | "network" | "connection",
  ) {
    super(message);
    this.name = "CallError";
  }
}

export interface CallHandlers {
  /** Events from our API: the agent's turns and the call lifecycle. */
  onVoiceEvent: (event: VoiceEvent) => void;
  /** Events from GPT-Live's data channel: live captions, start and close. */
  onLiveEvent: (event: LiveEvent) => void;
  /** The call ended or failed for a reason the stream couldn't report. */
  onFailure: (error: CallError) => void;
}

export interface Call {
  mic: MediaStream;
  agent: Promise<MediaStream>;
  setMuted: (muted: boolean) => void;
  hangUp: () => Promise<void>;
}

const ICE_GATHERING_TIMEOUT_MS = 2_000;

function waitForIceGathering(pc: RTCPeerConnection): Promise<void> {
  if (pc.iceGatheringState === "complete") return Promise.resolve();
  return new Promise((resolve) => {
    const done = () => {
      pc.removeEventListener("icegatheringstatechange", check);
      resolve();
    };
    const check = () => pc.iceGatheringState === "complete" && done();
    pc.addEventListener("icegatheringstatechange", check);
    setTimeout(done, ICE_GATHERING_TIMEOUT_MS);
  });
}

async function openMicrophone(): Promise<MediaStream> {
  try {
    return await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
  } catch {
    throw new CallError(
      "Microphone access is blocked. Allow it in your browser's site settings to start a call.",
      "microphone",
    );
  }
}

/**
 * Start a voice call in the given conversation.
 *
 * Audio flows directly between the browser and GPT-Live over WebRTC. Our API creates
 * the session (so the API key never reaches the browser), answers the SDP offer, and
 * keeps a server-side connection to the same session for the whole call; its stream
 * carries the agent's turns back here.
 */
export async function startCall(request: Omit<VoiceRequest, "sdp">, handlers: CallHandlers): Promise<Call> {
  const mic = await openMicrophone();
  const pc = new RTCPeerConnection();
  const abort = new AbortController();
  let ended = false;

  const agent = new Promise<MediaStream>((resolve) => {
    pc.addEventListener("track", (event) => resolve(event.streams[0] ?? new MediaStream([event.track])));
  });
  for (const track of mic.getTracks()) pc.addTrack(track, mic);

  const channel = pc.createDataChannel("oai-events");
  channel.addEventListener("message", (message) => {
    try {
      handlers.onLiveEvent(JSON.parse(String(message.data)) as LiveEvent);
    } catch {
      // ignore malformed frames
    }
  });
  pc.addEventListener("connectionstatechange", () => {
    if (pc.connectionState === "failed" && !ended) {
      handlers.onFailure(
        new CallError("The audio connection failed. A firewall or VPN may be blocking WebRTC.", "connection"),
      );
    }
  });

  const cleanup = () => {
    ended = true;
    abort.abort();
    channel.close();
    pc.close();
    for (const track of mic.getTracks()) track.stop();
  };

  try {
    await pc.setLocalDescription(await pc.createOffer());
    await waitForIceGathering(pc);
    const response = await fetch(`${API_URL}/voice`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify({ ...request, sdp: pc.localDescription?.sdp ?? "" } satisfies VoiceRequest),
      signal: abort.signal,
    });
    if (!response.ok || !response.body) throw new CallError(`The voice service returned ${response.status}.`, "network");
    void pump(response.body, pc, handlers, () => ended).finally(() => {
      if (!ended) cleanup();
    });
  } catch (error) {
    cleanup();
    if (error instanceof CallError) throw error;
    throw new CallError("Can't reach the voice service.", "network");
  }

  return {
    mic,
    agent,
    setMuted(muted) {
      if (channel.readyState === "open") {
        channel.send(JSON.stringify({ type: muted ? "session.input_audio.mute" : "session.input_audio.unmute" }));
      }
      for (const track of mic.getAudioTracks()) track.enabled = !muted;
    },
    async hangUp() {
      if (ended) return;
      // Ask GPT-Live to close gracefully so the server sees `session.closed` and ends its
      // stream; tear down regardless after a moment.
      if (channel.readyState === "open") channel.send(JSON.stringify({ type: "session.close" }));
      await new Promise((resolve) => setTimeout(resolve, 1_500));
      cleanup();
    },
  };
}

async function pump(
  body: NonNullable<Response["body"]>,
  pc: RTCPeerConnection,
  handlers: CallHandlers,
  isEnded: () => boolean,
): Promise<void> {
  const reader = body.pipeThrough(new TextDecoderStream()).getReader();
  const parser = new SSEParser();
  let finished = false;
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      for (const message of parser.feed(value)) {
        const event = JSON.parse(message.data) as VoiceEvent;
        if (event.type === "voice_session") {
          await pc.setRemoteDescription({ type: "answer", sdp: event.sdp });
        }
        if (event.type === "voice_ended") finished = true;
        handlers.onVoiceEvent(event);
      }
    }
  } catch {
    // aborted on hang-up, or the network dropped: handled below
  }
  if (!finished && !isEnded()) {
    handlers.onFailure(new CallError("The call dropped.", "network"));
  }
}
