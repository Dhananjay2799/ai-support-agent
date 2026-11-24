"use client";

import { ArrowUp, Square } from "lucide-react";
import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { Message } from "@/components/chat/message";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { type AssistantTurn, type Turn, applyEvent, newAssistantTurn } from "@/lib/chat-state";
import { ChatStreamError, streamChat } from "@/lib/chat-client";

const SESSION_KEY = "acme-support-session";
const SUGGESTIONS = [
  "I was charged twice this month",
  "What's the refund policy on Pro?",
  "How do I reset two-factor authentication?",
];

function getSessionId(): string {
  const existing = sessionStorage.getItem(SESSION_KEY);
  if (existing) return existing;
  const created = crypto.randomUUID();
  sessionStorage.setItem(SESSION_KEY, created);
  return created;
}

export function ChatPanel() {
  const [turns, setTurns] = useState<Turn[]>([]);
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
          { session_id: getSessionId(), message },
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
    [busy, updateAssistant],
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
            <div className="flex flex-col items-start gap-3 pt-16">
              <h2 className="text-lg font-medium">How can we help?</h2>
              <p className="text-sm text-muted-foreground">
                Ask about billing, refunds, your plan, or account security.
              </p>
              <div className="flex flex-wrap gap-2 pt-2">
                {SUGGESTIONS.map((suggestion) => (
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

      <form onSubmit={onSubmit} className="border-t bg-background">
        <div className="mx-auto flex max-w-3xl items-center gap-2 px-4 py-3">
          <Input
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="Message Acme Cloud support…"
            aria-label="Message"
            maxLength={4000}
            disabled={busy}
          />
          {busy ? (
            <Button type="button" size="icon" variant="outline" aria-label="Stop" onClick={() => abortRef.current?.abort()}>
              <Square />
            </Button>
          ) : (
            <Button type="submit" size="icon" aria-label="Send" disabled={!draft.trim()}>
              <ArrowUp />
            </Button>
          )}
        </div>
      </form>
    </div>
  );
}
