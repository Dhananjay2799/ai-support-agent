"""Per-turn traces: what a turn did, in order, with timing, tokens and cost.

A `Tracer` travels with the turn in a ContextVar, like the usage meter. Any code the
turn runs (the model call, a tool, retrieval, the judge) opens a span with `span(...)`;
spans nest by context, so a judge call made inside a tool shows up under that tool.
Outside a turn there is no tracer, and `span(...)` costs nothing.
"""

import contextlib
import time
import uuid
from collections.abc import Iterator
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from app.contracts import Span, SpanKind

MAX_TEXT = 2_000


def clip(value: Any, limit: int = MAX_TEXT) -> Any:
    """Shorten long strings for a trace; leave everything else alone."""
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + f"… [{len(value) - limit} more characters]"
    return value


@dataclass
class SpanRecord:
    id: str
    parent_id: str | None
    name: str
    kind: SpanKind
    start_ms: float
    end_ms: float | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    def set(self, **attributes: Any) -> None:
        self.attributes.update(attributes)


class Tracer:
    def __init__(self) -> None:
        self._started = time.perf_counter()
        self.records: list[SpanRecord] = []

    def now_ms(self) -> float:
        return round((time.perf_counter() - self._started) * 1000, 1)

    @contextlib.contextmanager
    def span(self, name: str, kind: SpanKind, **attributes: Any) -> Iterator[SpanRecord]:
        record = SpanRecord(
            id=uuid.uuid4().hex[:12],
            parent_id=_current_span.get(),
            name=name,
            kind=kind,
            start_ms=self.now_ms(),
            attributes=dict(attributes),
        )
        self.records.append(record)
        token = _current_span.set(record.id)
        try:
            yield record
        except BaseException as error:
            record.attributes.setdefault("status", "error")
            record.attributes.setdefault("error", clip(f"{type(error).__name__}: {error}", 300))
            raise
        finally:
            record.end_ms = self.now_ms()
            with contextlib.suppress(ValueError):  # closed from another context (a cancelled stream)
                _current_span.reset(token)

    def spans(self) -> list[Span]:
        return [
            Span(
                id=r.id,
                parent_id=r.parent_id,
                name=r.name,
                kind=r.kind,
                start_ms=r.start_ms,
                end_ms=r.end_ms if r.end_ms is not None else self.now_ms(),
                attributes=r.attributes,
            )
            for r in self.records
        ]


current_tracer: ContextVar[Tracer | None] = ContextVar("current_tracer", default=None)
_current_span: ContextVar[str | None] = ContextVar("current_span", default=None)


@contextlib.contextmanager
def span(name: str, kind: SpanKind, **attributes: Any) -> Iterator[SpanRecord]:
    """A span in the current turn's trace, or a throwaway record outside a turn."""
    tracer = current_tracer.get()
    if tracer is None:
        yield SpanRecord(id="", parent_id=None, name=name, kind=kind, start_ms=0, attributes=dict(attributes))
        return
    with tracer.span(name, kind, **attributes) as record:
        yield record
