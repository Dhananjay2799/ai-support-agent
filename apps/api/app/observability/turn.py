"""Everything one agent turn reports about itself: usage, cost and a trace.

Both entry points (the chat route and a voice call) run a turn the same way:

    with TurnTelemetry(channel="text", message=...) as telemetry:
        async for event in run_turn(...):
            if event.type == "done":
                yield telemetry.trace(event.turn_id)   # the trace goes out just before done
            yield event

then `save_trace` stores the spans for later inspection.
"""

import contextlib
import json
import logging
from types import TracebackType
from typing import Any

import anyio
from psycopg import AsyncConnection
from psycopg.rows import DictRow

from app.contracts import TraceEvent
from app.db import get_pool
from app.observability.tracing import SpanRecord, Tracer, clip, current_tracer
from app.observability.usage import UsageMeter, current_meter

log = logging.getLogger(__name__)


class TurnTelemetry:
    def __init__(self, **root_attributes: Any) -> None:
        self.meter = UsageMeter()
        self.tracer = Tracer()
        self._root_attributes = {k: clip(v) for k, v in root_attributes.items()}
        self._root: contextlib.AbstractContextManager[SpanRecord] | None = None
        self._root_record: SpanRecord | None = None
        self._tokens: list[Any] = []

    def __enter__(self) -> "TurnTelemetry":
        self._tokens = [current_meter.set(self.meter), current_tracer.set(self.tracer)]
        self._root = self.tracer.span("turn", "node", **self._root_attributes)
        self._root_record = self._root.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._close_root()
        with contextlib.suppress(ValueError):  # a cancelled stream may close us from another context
            current_tracer.reset(self._tokens[1])
            current_meter.reset(self._tokens[0])

    def _close_root(self) -> None:
        if self._root is not None:
            root, self._root = self._root, None
            with contextlib.suppress(ValueError):
                root.__exit__(None, None, None)

    def annotate(self, **attributes: Any) -> None:
        """Add attributes to the turn's root span (e.g. a voice request once it's known)."""
        if self._root_record is not None:
            self._root_record.set(**{k: clip(v) for k, v in attributes.items()})

    def trace(self, turn_id: str) -> TraceEvent:
        """Close the turn's root span (with totals) and return the whole trace."""
        if self._root_record is not None:
            self._root_record.set(
                input_tokens=self.meter.input_tokens,
                cached_tokens=self.meter.cached_tokens,
                output_tokens=self.meter.output_tokens,
                cost_usd=round(self.meter.cost_usd, 6),
                model_calls=len(self.meter.calls),
            )
        self._close_root()
        return TraceEvent(turn_id=turn_id, spans=self.tracer.spans())


async def save_trace(conn: AsyncConnection[DictRow], session_id: str, trace: TraceEvent) -> None:
    async with conn.cursor() as cur:
        await cur.executemany(
            """
            insert into public.traces
              (session_id, turn_id, span_id, parent_span_id, name, kind, start_ms, end_ms, attributes)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [
                (
                    session_id,
                    trace.turn_id,
                    s.id,
                    s.parent_id,
                    s.name,
                    s.kind,
                    s.start_ms,
                    s.end_ms,
                    json.dumps(s.attributes),
                )
                for s in trace.spans
            ],
        )


async def persist_trace(session_id: str, trace: TraceEvent) -> None:
    """Store a turn's spans. Best effort: a failure here must never fail the turn."""
    with anyio.move_on_after(5, shield=True):
        try:
            pool = await get_pool()
            async with pool.connection() as conn:
                await save_trace(conn, session_id, trace)
        except Exception:
            log.warning("could not save the trace of turn %s", trace.turn_id, exc_info=True)


async def load_traces(conn: AsyncConnection[DictRow], session_id: str) -> list[dict[str, Any]]:
    """A session's turns, oldest first, each with its spans in start order."""
    cur = await conn.execute(
        """
        select turn_id, min(created_at) as created_at,
               jsonb_agg(jsonb_build_object(
                 'id', span_id, 'parent_id', parent_span_id, 'name', name, 'kind', kind,
                 'start_ms', start_ms, 'end_ms', end_ms, 'attributes', attributes
               ) order by start_ms, id) as spans
        from public.traces
        where session_id = %s
        group by turn_id
        order by min(created_at)
        """,
        (session_id,),
    )
    return [dict(row) for row in await cur.fetchall()]
