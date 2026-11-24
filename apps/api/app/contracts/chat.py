from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: UUID = Field(description="One id per conversation, shared by chat and voice")
    message: str = Field(min_length=1, max_length=4000)
    persona_id: str | None = Field(
        default=None,
        max_length=64,
        description="Demo persona acting as the signed-in customer",
    )
