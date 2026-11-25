"""GPT-Live session setup for a support call.

The voice model only listens, talks and handles interruptions. Every request about the
account or the product is delegated to our backend, which answers through the same
agent graph as chat. So the voice prompt holds conversation rules, not business rules.
"""

from collections.abc import Sequence
from typing import Any, Final

from openai.types.live import ClientConfigParam, InitialItemParam, MediaSessionConfigParam

LIVE_MODEL: Final = "gpt-live-1.1"
LIVE_VOICE: Final = "marin"
MAX_CALL_SECONDS: Final = 180  # well inside the 300 s serverless limit that holds the sideband
HISTORY_MESSAGES: Final = 20
HISTORY_CHARS: Final = 1_200

INSTRUCTIONS = """\
# Role
You are the voice of Acme Cloud customer support on a phone call. You sound warm, calm and
efficient.

# Delegate, don't answer
For anything about the caller's account, billing, refunds, plans, policies, security or the
product, delegate to the backend and wait for its result. Never answer those from your own
knowledge, and never promise an outcome the backend hasn't confirmed. If the caller gives an
email or ZIP code, delegate so the backend can verify it.

When the backend's result arrives, say it naturally in your own words. Keep every amount, date,
plan name and ticket id exact. Read ticket ids character by character, like "E S C, one zero
zero one". If the backend says it handed the case to a specialist, tell the caller that.

You may handle greetings, thanks, "can you repeat that" and goodbyes yourself.

# Conversation
- Keep replies to one to three short sentences.
- If the caller interrupts, stop and listen. Deal with what they said next; don't go back to
  finish your earlier sentence unless they ask you to.
- If you didn't catch something, ask them to repeat it.
- When the call connects, greet the caller{name_clause} in one short sentence and ask how you
  can help.
"""

# The browser is untrusted: it may only hang up and mute. Speaking for the backend
# (commentary, instructions) is reserved for the server's sideband connection.
BROWSER_CHANNEL: Final[ClientConfigParam] = {
    "data_channel": {
        "allowed_client_events": ["session.close", "session.input_audio.mute", "session.input_audio.unmute"],
        "allowed_server_events": [
            {"type": event}
            for event in (
                "session.started",
                "session.closed",
                "session.input_transcript.delta",
                "session.output_transcript.delta",
                "session.input_audio.muted",
                "session.input_audio.unmuted",
                "session.paused",
                "session.resumed",
                "error",
            )
        ],
    }
}


def live_instructions(caller_first_name: str | None) -> str:
    return INSTRUCTIONS.format(name_clause=f" by name ({caller_first_name})" if caller_first_name else "")


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") in ("output_text", "input_text", "text")
        )
    return ""


def history_from_items(items: Sequence[dict[str, Any]]) -> list[InitialItemParam]:
    """The chat so far as Live's text-only starting history.

    Keeps user and assistant messages (not tool calls or reasoning), most recent last,
    within Live's limits on message count and length.
    """
    history: list[InitialItemParam] = []
    for item in items:
        role = item.get("role")
        if item.get("type", "message") != "message" or role not in ("user", "assistant"):
            continue
        text = _text(item.get("content")).strip()
        if not text:
            continue
        if len(text) > HISTORY_CHARS:
            text = text[: HISTORY_CHARS - 1] + "…"
        if role == "user":
            history.append(
                {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}
            )
        else:
            history.append(
                {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}
            )
    return history[-HISTORY_MESSAGES:]


def session_config(
    *, caller_first_name: str | None, history: list[InitialItemParam]
) -> MediaSessionConfigParam:
    config: MediaSessionConfigParam = {
        "model": LIVE_MODEL,
        "instructions": live_instructions(caller_first_name),
        "audio": {"output": {"voice": LIVE_VOICE}},
        "delegation": {"type": "client"},
        "client": BROWSER_CHANNEL,
    }
    if history:
        config["input"] = history
    return config
