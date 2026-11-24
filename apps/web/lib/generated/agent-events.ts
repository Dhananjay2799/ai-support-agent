/* Generated from contracts/agent-events.schema.json by npm run gen:types. Do not edit. */

/**
 * This interface was referenced by `Contracts`'s JSON-Schema
 * via the `definition` "AgentEvent".
 */
export type AgentEvent =
  TokenEvent | StepEvent | GuardrailEvent | EscalationEvent | CitationsEvent | TraceEvent | ErrorEvent | DoneEvent;
/**
 * This interface was referenced by `Contracts`'s JSON-Schema
 * via the `definition` "JsonValue".
 */
export type JsonValue = unknown;

/**
 * Generated from apps/api/app/contracts. Do not edit by hand.
 */
export interface Contracts {
  event?: AgentEvent;
  chatRequest?: ChatRequest;
}
/**
 * A chunk of the assistant's reply text.
 *
 * This interface was referenced by `Contracts`'s JSON-Schema
 * via the `definition` "TokenEvent".
 */
export interface TokenEvent {
  type: "token";
  text: string;
}
/**
 * A tool started, completed or failed. `id` is unique per tool invocation.
 *
 * This interface was referenced by `Contracts`'s JSON-Schema
 * via the `definition` "StepEvent".
 */
export interface StepEvent {
  type: "step";
  id: string;
  tool: string;
  /**
   * Human-readable status, e.g. 'Verifying account tier…'
   */
  label: string;
  status: "started" | "completed" | "failed";
  duration_ms: number | null;
  detail: string | null;
}
/**
 * Outcome of the confidence gate for the current answer.
 *
 * This interface was referenced by `Contracts`'s JSON-Schema
 * via the `definition` "GuardrailEvent".
 */
export interface GuardrailEvent {
  type: "guardrail";
  confidence: number;
  decision: "answer" | "verify" | "escalate";
  reason: string | null;
}
/**
 * The conversation was handed to a human; the agent stops taking actions.
 *
 * This interface was referenced by `Contracts`'s JSON-Schema
 * via the `definition` "EscalationEvent".
 */
export interface EscalationEvent {
  type: "escalation";
  ticket_id: string;
  reason: string;
  priority: "low" | "normal" | "high" | "urgent";
}
/**
 * Knowledge-base passages the answer cited, after checking they were retrieved.
 *
 * This interface was referenced by `Contracts`'s JSON-Schema
 * via the `definition` "CitationsEvent".
 */
export interface CitationsEvent {
  type: "citations";
  citations: Citation[];
}
/**
 * This interface was referenced by `Contracts`'s JSON-Schema
 * via the `definition` "Citation".
 */
export interface Citation {
  /**
   * Passage id, e.g. 'refunds-and-billing#5'
   */
  id: string;
  doc_title: string;
  section: string;
}
/**
 * This interface was referenced by `Contracts`'s JSON-Schema
 * via the `definition` "TraceEvent".
 */
export interface TraceEvent {
  type: "trace";
  span: Span;
}
/**
 * This interface was referenced by `Contracts`'s JSON-Schema
 * via the `definition` "Span".
 */
export interface Span {
  id: string;
  parent_id: string | null;
  name: string;
  kind: "node" | "llm" | "tool" | "retrieval" | "voice";
  /**
   * Milliseconds since the turn started
   */
  start_ms: number;
  end_ms: number | null;
  attributes: {
    [k: string]: JsonValue;
  };
}
/**
 * This interface was referenced by `Contracts`'s JSON-Schema
 * via the `definition` "ErrorEvent".
 */
export interface ErrorEvent {
  type: "error";
  code: "llm_timeout" | "rate_limited" | "tool_failed" | "budget_exceeded" | "internal";
  message: string;
  retryable: boolean;
}
/**
 * Always the last event of a turn.
 *
 * This interface was referenced by `Contracts`'s JSON-Schema
 * via the `definition` "DoneEvent".
 */
export interface DoneEvent {
  type: "done";
  turn_id: string;
  usage: Usage;
  cost_usd: number;
  latency_ms: number;
}
/**
 * This interface was referenced by `Contracts`'s JSON-Schema
 * via the `definition` "Usage".
 */
export interface Usage {
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
}
/**
 * This interface was referenced by `Contracts`'s JSON-Schema
 * via the `definition` "ChatRequest".
 */
export interface ChatRequest {
  /**
   * One id per conversation, shared by chat and voice
   */
  session_id: string;
  message: string;
  /**
   * Demo persona acting as the signed-in customer
   */
  persona_id?: string | null;
}
