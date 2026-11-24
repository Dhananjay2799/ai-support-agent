import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.contracts import DoneEvent, StepEvent, TokenEvent, agent_event_adapter
from scripts.export_contracts import OUT, build_schema


def test_union_round_trips_by_discriminator() -> None:
    step = StepEvent(id="s1", tool="verify_customer", label="Verifying…", status="started")
    parsed = agent_event_adapter.validate_json(step.model_dump_json())
    assert isinstance(parsed, StepEvent)
    assert parsed == step


def test_unknown_event_type_is_rejected() -> None:
    with pytest.raises(ValidationError):
        agent_event_adapter.validate_python({"type": "mystery", "text": "hi"})


def test_extra_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        TokenEvent.model_validate({"type": "token", "text": "hi", "surprise": True})


def test_type_is_required_in_serialization_schema() -> None:
    schema = DoneEvent.model_json_schema(mode="serialization")
    assert "type" in schema["required"]


def test_committed_schema_is_up_to_date() -> None:
    """Fails if someone changes a contract without re-running the exporter."""
    committed = json.loads(Path(OUT).read_text(encoding="utf-8"))
    assert committed == build_schema(), "run: uv run python -m scripts.export_contracts"
