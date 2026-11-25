# Voice

Press the phone button to talk to the same agent. GPT-Live (`gpt-live-1.1`) does the listening,
speaking and turn-taking; our backend makes every decision.

```text
browser ──WebRTC audio──▶ GPT-Live ◀──sideband WebSocket── API ──▶ same LangGraph agent + memory
   ▲  data channel: captions          (delegations, spoken answers)        │
   └──────────── SSE: SDP answer, then each turn's steps, citations, tickets ┘
```

1. The browser posts its WebRTC offer to `POST /voice`. The API creates the GPT-Live session (the
   API key never reaches the browser), seeded with the chat so far, and streams back the answer.
2. The same request then attaches a **sideband** to the session and stays on it for the call.
   With client delegation, GPT-Live hands over each request; the delegation carries no text, so
   the API rebuilds what the caller said from transcript deltas and runs it through the agent
   graph on the conversation's thread. The answer goes back as `session.commentary.append`, and
   GPT-Live says it in its own words while the steps, sources and any ticket appear on screen.
3. Typing after the call continues the same conversation, and a call started mid-chat knows what
   was said.

**Interruptions.** GPT-Live stops talking when the caller cuts in. Requests run in order, so two
turns never write the same memory at once; if the caller has moved on when a turn finishes, its
result goes in as silent context (`session.thinking.append`) instead of answering a question
they abandoned. The UI marks a cut-off line as *interrupted*. The rule was tuned on a recorded
call (`apps/web/lib/__fixtures__/voice-interruption.json`), where transcript timing showed the
caller's words landing a second after the agent's last ones.

**Safety and limits.** The browser's data channel may only hang up and mute; only the server can
speak for the backend. Calls are capped at 3 minutes so they end well inside the 300 s serverless
limit that holds the sideband, and if the browser leaves, the API hangs the session up.

**Tested with real speech.** `apps/api/scripts/voice_e2e.py` places a real GPT-Live call with
synthesized speech (over WebSocket, since WebRTC needs UDP): a chat turn first, then by voice
"I was charged twice", a $29 refund, the caller talking over the answer with a new question, and
a chat turn after the call that knows about the refund. The voice acknowledges within a
second of the caller finishing; a full refund answer is ready about 5 s later.
