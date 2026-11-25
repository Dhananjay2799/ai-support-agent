"use client";

import { ArrowUp, Phone, Square, UserRound } from "lucide-react";
import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { Message } from "@/components/chat/message";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { CallPanel } from "@/components/voice/call-panel";
import { useVoiceCall } from "@/components/voice/use-voice-call";
import { type AssistantTurn, type Turn, applyEvent, newAssistantTurn } from "@/lib/chat-state";
import { ChatStreamError, streamChat } from "@/lib/chat-client";
import type { AgentEvent } from "@/lib/generated/agent-events";
import { type Persona, apiPersonaId } from "@/lib/personas";

/** One conversation. Remount it (new `key`) to start over with a fresh session. */
export function ChatPanel({ persona }: { persona: Persona }) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const sessionRef = useRef<string | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Block body on purpose: newer browsers return a Promise from scrollIntoView,
    // and React would treat a returned value as the effect's cleanup function.
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns]);
  useEffect(() => () => abortRef.current?.abort(), []);

  const updateAssistant = useCallback((id: string, update: (turn: AssistantTurn) => AssistantTurn) => {
    setTurns((all) => all.map((t) => (t.role === "assistant" && t.id === id ? update(t) : t)));
  }, []);
  const sessionId = useCallback(() => (sessionRef.current ??= crypto.randomUUID()), []);

  // A voice call joins this same conversation: each spoken request and the agent's
  // turn for it are added to the list like typed ones.
  const voiceTurnRef = useRef<string | null>(null);
  const onVoiceTurn = useCallback((utterance: string) => {
    const assistantId = crypto.randomUUID();
    voiceTurnRef.current = assistantId;
    setTurns((all) => [
      ...all,
      { role: "user", id: crypto.randomUUID(), text: utterance, via: "voice" },
      newAssistantTurn(assistantId, "voice"),
    ]);
  }, []);
  const onVoiceAgentEvent = useCallback(
    (event: AgentEvent) => {
      const id = voiceTurnRef.current;
      if (!id) return false;
      updateAssistant(id, (turn) => applyEvent(turn, event));
      if (event.type === "done") voiceTurnRef.current = null;
      return true;
    },
    [updateAssistant],
  );
  const call = useVoiceCall({
    sessionId,
    personaId: apiPersonaId(persona),
    onVoiceTurn,
    onAgentEvent: onVoiceAgentEvent,
  });
  const onCall = call.state?.status === "connecting" || call.state?.status === "live";

  const send = useCallback(
    async (text: string) => {
      const message = text.trim();
      if (!message || busy) return;

      const assistantId = crypto.randomUUID();
      setTurns((all) => [
        ...all,
        { role: "user", id: crypto.randomUUID(), text: message },
        newAssistantTurn(assistantId),
      ]);
      setDraft("");
      setBusy(true);

      const controller = new AbortController();
      abortRef.current = controller;
      try {
        await streamChat(
          { session_id: sessionId(), message, persona_id: apiPersonaId(persona) },
          {
            signal: controller.signal,
            onEvent: (event) => updateAssistant(assistantId, (turn) => applyEvent(turn, event)),
          },
        );
      } catch (error) {
        const stopped = controller.signal.aborted;
        const messageText = stopped
          ? "Stopped."
          : error instanceof ChatStreamError
            ? error.message
            : "Something went wrong.";
        updateAssistant(assistantId, (turn) => ({
          ...turn,
          status: "error",
          error: { message: messageText, retryable: !stopped },
        }));
      } finally {
        setBusy(false);
        abortRef.current = null;
      }
    },
    [busy, persona, sessionId, updateAssistant],
  );

  const retryLast = useCallback(() => {
    const lastUser = [...turns].reverse().find((t) => t.role === "user");
    if (lastUser) void send(lastUser.text);
  }, [turns, send]);

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void send(draft);
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto flex max-w-3xl flex-col gap-4 px-4 py-6">
          {turns.length === 0 ? (
            <div className="flex flex-col items-start gap-3 pt-12">
              <h2 className="text-lg font-medium">How can we help?</h2>
              <p className="text-sm text-muted-foreground">
                Ask about billing, refunds, your plan, or account security.
              </p>
              <div className="flex w-full items-start gap-3 rounded-xl border bg-muted/30 p-3 text-xs">
                <UserRound className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
                <div className="space-y-0.5">
                  <p className="font-medium">
                    {persona.id === "guest"
                      ? "Not signed in"
                      : `Signed in as ${persona.name} · ${persona.plan} · ${persona.status}`}
                  </p>
                  <p className="text-muted-foreground">{persona.scenario}</p>
                </div>
              </div>
              <div className="flex flex-wrap gap-2 pt-1">
                {persona.suggestions.map((suggestion) => (
                  <Button key={suggestion} variant="outline" size="sm" onClick={() => void send(suggestion)}>
                    {suggestion}
                  </Button>
                ))}
              </div>
            </div>
          ) : (
            turns.map((turn, index) => (
              <Message
                key={turn.id}
                turn={turn}
                onRetry={index === turns.length - 1 && !busy ? retryLast : undefined}
              />
            ))
          )}
          <div ref={endRef} />
        </div>
      </div>

      <CallPanel call={call} onRetry={() => void call.start()} />

      <form onSubmit={onSubmit} className="border-t bg-background">
        <div className="mx-auto flex max-w-3xl items-center gap-2 px-4 py-3">
          <Input
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder={onCall ? "On a call. Hang up to type." : "Message Acme Cloud support…"}
            aria-label="Message"
            maxLength={4000}
            disabled={busy || onCall}
          />
          {!busy && (
            <Button
              type="button"
              size="icon"
              variant="outline"
              aria-label="Start a voice call"
              title="Talk to the agent"
              disabled={onCall}
              onClick={() => void call.start()}
            >
              <Phone />
            </Button>
          )}
          {busy ? (
            <Button type="button" size="icon" variant="outline" aria-label="Stop" onClick={() => abortRef.current?.abort()}>
              <Square />
            </Button>
          ) : (
            <Button type="submit" size="icon" aria-label="Send" disabled={!draft.trim() || onCall}>
              <ArrowUp />
            </Button>
          )}
        </div>
      </form>
    </div>
  );
}
