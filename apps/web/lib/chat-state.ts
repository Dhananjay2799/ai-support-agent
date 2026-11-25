import type {
  AgentEvent,
  Citation,
  DoneEvent,
  ErrorEvent,
  EscalationEvent,
  GuardrailEvent,
  StepEvent,
  TraceEvent,
} from "@/lib/generated/agent-events";

export interface Step {
  id: string;
  tool: string;
  label: string;
  status: StepEvent["status"];
  durationMs: number | null;
  detail: string | null;
}

export interface UserTurn {
  role: "user";
  id: string;
  text: string;
  via?: "voice";
}

export interface AssistantTurn {
  role: "assistant";
  id: string;
  via?: "voice";
  text: string;
  steps: Step[];
  status: "streaming" | "done" | "error";
  guardrail?: GuardrailEvent;
  escalation?: EscalationEvent;
  citations?: Citation[];
  trace?: TraceEvent;
  error?: Pick<ErrorEvent, "message" | "retryable">;
  done?: DoneEvent;
}

export type Turn = UserTurn | AssistantTurn;

export function newAssistantTurn(id: string, via?: "voice"): AssistantTurn {
  return { role: "assistant", id, text: "", steps: [], status: "streaming", ...(via && { via }) };
}

function upsertStep(steps: Step[], event: StepEvent): Step[] {
  const next: Step = {
    id: event.id,
    tool: event.tool,
    label: event.label,
    status: event.status,
    durationMs: event.duration_ms,
    detail: event.detail,
  };
  const index = steps.findIndex((step) => step.id === event.id);
  if (index === -1) return [...steps, next];
  return steps.map((step, i) => (i === index ? next : step));
}

/** Pure reducer: fold one streamed event into the assistant turn. */
export function applyEvent(turn: AssistantTurn, event: AgentEvent): AssistantTurn {
  switch (event.type) {
    case "token":
      return { ...turn, text: turn.text + event.text };
    case "step":
      return { ...turn, steps: upsertStep(turn.steps, event) };
    case "guardrail":
      return { ...turn, guardrail: event };
    case "escalation":
      return { ...turn, escalation: event };
    case "citations":
      return { ...turn, citations: event.citations };
    case "error":
      return { ...turn, status: "error", error: { message: event.message, retryable: event.retryable } };
    case "done":
      return { ...turn, status: turn.status === "error" ? "error" : "done", done: event };
    case "trace":
      return { ...turn, trace: event };
  }
}
