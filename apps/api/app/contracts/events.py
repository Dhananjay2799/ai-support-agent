"""Wire contract for everything the API streams to the UI.

`AgentEvent` is what one agent turn streams, in chat and in voice alike. `VoiceEvent`
adds the call's own events around those turns. The JSON Schema exported from both
(`scripts/export_contracts.py`) generates the TypeScript types in
`apps/web/lib/generated/agent-events.ts`, so both sides always agree on the shape.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter


class _Event(BaseModel):
    # Fields with defaults (like `type`) are still always serialized, so mark them
    # required in the serialization schema; this keeps `type` non-optional in TS.
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)


StepStatus = Literal["started", "completed", "failed"]
GuardrailDecision = Literal["answer", "verify", "escalate"]
ErrorCode = Literal["llm_timeout", "rate_limited", "tool_failed", "budget_exceeded", "internal"]
SpanKind = Literal["node", "llm", "tool", "retrieval", "voice"]
Priority = Literal["low", "normal", "high", "urgent"]


class Usage(_Event):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cached_tokens: int = Field(default=0, ge=0)


class Span(_Event):
    id: str
    parent_id: str | None = None
    name: str
    kind: SpanKind
    start_ms: float = Field(description="Milliseconds since the turn started")
    end_ms: float | None = None
    attributes: dict[str, JsonValue] = Field(default_factory=dict)


class TokenEvent(_Event):
    """A chunk of the assistant's reply text."""

    type: Literal["token"] = "token"
    text: str


class StepEvent(_Event):
    """A tool started, completed or failed. `id` is unique per tool invocation."""

    type: Literal["step"] = "step"
    id: str
    tool: str
    label: str = Field(description="Human-readable status, e.g. 'Verifying account tier…'")
    status: StepStatus
    duration_ms: float | None = None
    detail: str | None = None


class GuardrailEvent(_Event):
    """Outcome of the confidence gate for the current answer."""

    type: Literal["guardrail"] = "guardrail"
    confidence: float = Field(ge=0, le=1)
    decision: GuardrailDecision
    reason: str | None = None


class EscalationEvent(_Event):
    """The conversation was handed to a human; the agent stops taking actions."""

    type: Literal["escalation"] = "escalation"
    ticket_id: str
    reason: str
    priority: Priority = "normal"
    first_response_hours: int = Field(description="First-reply target for the customer's plan")


class Citation(_Event):
    id: str = Field(description="Passage id, e.g. 'refunds-and-billing#5'")
    doc_title: str
    section: str


class CitationsEvent(_Event):
    """Knowledge-base passages the answer cited, after checking they were retrieved."""

    type: Literal["citations"] = "citations"
    citations: list[Citation]


class TraceEvent(_Event):
    type: Literal["trace"] = "trace"
    span: Span


class ErrorEvent(_Event):
    type: Literal["error"] = "error"
    code: ErrorCode
    message: str
    retryable: bool


class DoneEvent(_Event):
    """Always the last event of a turn."""

    type: Literal["done"] = "done"
    turn_id: str
    usage: Usage = Field(default_factory=Usage)
    cost_usd: float = Field(default=0, ge=0)
    latency_ms: float = Field(ge=0)


_AgentEvents = (
    TokenEvent
    | StepEvent
    | GuardrailEvent
    | EscalationEvent
    | CitationsEvent
    | TraceEvent
    | ErrorEvent
    | DoneEvent
)
AgentEvent = Annotated[_AgentEvents, Field(discriminator="type")]

agent_event_adapter: TypeAdapter[AgentEvent] = TypeAdapter(AgentEvent)


# --- Voice calls ---------------------------------------------------------------------

VoiceEndReason = Literal["hangup", "time_limit", "closed", "error"]


class VoiceSessionEvent(_Event):
    """First event of a call: the WebRTC answer for the browser's offer."""

    type: Literal["voice_session"] = "voice_session"
    live_session_id: str
    sdp: str = Field(description="SDP answer to apply as the peer connection's remote description")
    max_seconds: int = Field(description="The call is closed after this long")


class VoiceTurnEvent(_Event):
    """The customer's spoken request, sent before the agent turn that handles it."""

    type: Literal["voice_turn"] = "voice_turn"
    utterance: str


class VoiceEndedEvent(_Event):
    type: Literal["voice_ended"] = "voice_ended"
    reason: VoiceEndReason
    duration_ms: float


VoiceEvent = Annotated[
    VoiceSessionEvent | VoiceTurnEvent | VoiceEndedEvent | _AgentEvents, Field(discriminator="type")
]

voice_event_adapter: TypeAdapter[VoiceEvent] = TypeAdapter(VoiceEvent)
