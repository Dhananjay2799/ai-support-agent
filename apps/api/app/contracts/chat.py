from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

SimulatedFailure = Literal["llm_timeout", "rate_limit", "tool_error", "tool_outage"]


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: UUID = Field(description="One id per conversation, shared by chat and voice")
    message: str = Field(min_length=1, max_length=4000)
    persona_id: str | None = Field(
        default=None,
        max_length=64,
        description="Demo persona acting as the signed-in customer",
    )
    simulate: SimulatedFailure | None = Field(
        default=None,
        description="Demo only: inject one failure into this turn to show how it's recovered",
    )


class VoiceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: UUID = Field(description="The conversation the call joins; same id as chat")
    persona_id: str | None = Field(default=None, max_length=64)
    sdp: str = Field(min_length=1, max_length=20_000, description="The browser's WebRTC SDP offer")
