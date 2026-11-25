"""Turn failures into typed, user-safe error events (shared by chat and voice)."""

import openai
import psycopg

from app.contracts import ErrorEvent


def error_event(error: Exception) -> ErrorEvent:
    if isinstance(error, openai.APITimeoutError | openai.APIConnectionError):
        return ErrorEvent(
            code="llm_timeout", message="The AI service took too long to respond.", retryable=True
        )
    if isinstance(error, openai.RateLimitError):
        return ErrorEvent(code="rate_limited", message="The AI service is busy right now.", retryable=True)
    if isinstance(error, psycopg.OperationalError):
        return ErrorEvent(
            code="internal", message="Our account system is unreachable right now.", retryable=True
        )
    return ErrorEvent(code="internal", message="Something went wrong on our side.", retryable=True)
