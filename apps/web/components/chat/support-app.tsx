"use client";

import { Activity, SquarePen } from "lucide-react";
import { useState } from "react";

import { ChatPanel } from "@/components/chat/chat-panel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DEFAULT_PERSONA, PERSONAS, personaById } from "@/lib/personas";

export function SupportApp() {
  const [persona, setPersona] = useState(DEFAULT_PERSONA);
  const [conversation, setConversation] = useState(0);
  const [tracesOpen, setTracesOpen] = useState(false);

  return (
    <div className="flex h-dvh flex-col">
      <header className="border-b">
        <div className="flex items-center gap-2 px-4 py-3 sm:gap-3 lg:px-6">
          <div className="flex size-7 shrink-0 items-center justify-center rounded-md bg-primary text-xs font-semibold text-primary-foreground">
            A
          </div>
          <h1 className="hidden text-sm font-medium whitespace-nowrap min-[480px]:block">Acme Cloud Support</h1>
          <Badge variant="outline" className="hidden sm:inline-flex">
            AI agent demo
          </Badge>

          <label className="ml-auto flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
            <span className="hidden sm:inline">Customer</span>
            <select
              value={persona.id}
              onChange={(event) => {
                setPersona(personaById(event.target.value));
                setConversation((n) => n + 1);
              }}
              className="h-8 min-w-0 rounded-lg border bg-background px-2 text-sm text-foreground outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
              aria-label="Demo customer"
            >
              {PERSONAS.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.id === "guest" ? "Guest (not signed in)" : `${p.name} · ${p.plan}`}
                </option>
              ))}
            </select>
          </label>
          <Button
            variant={tracesOpen ? "secondary" : "outline"}
            size="sm"
            aria-pressed={tracesOpen}
            title="Traces, latency, tokens and cost"
            onClick={() => setTracesOpen((open) => !open)}
          >
            <Activity data-icon="inline-start" />
            <span className="hidden sm:inline">Traces</span>
            <span className="sr-only sm:hidden">Traces</span>
          </Button>
          <Button variant="outline" size="sm" onClick={() => setConversation((n) => n + 1)}>
            <SquarePen data-icon="inline-start" />
            <span className="hidden sm:inline">New conversation</span>
            <span className="sr-only sm:hidden">New conversation</span>
          </Button>
        </div>
      </header>
      <ChatPanel
        key={`${persona.id}:${conversation}`}
        persona={persona}
        tracesOpen={tracesOpen}
        onTracesOpenChange={setTracesOpen}
      />
    </div>
  );
}
