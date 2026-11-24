"""Export the wire contract as JSON Schema for the web app's type generator.

Usage: uv run python -m scripts.export_contracts  (writes /contracts/agent-events.schema.json)
"""

import json
from pathlib import Path
from typing import Any

from app.contracts import ChatRequest, agent_event_adapter

REPO_ROOT = Path(__file__).resolve().parents[3]
OUT = REPO_ROOT / "contracts" / "agent-events.schema.json"


def _strip_field_titles(node: Any) -> Any:
    """Drop Pydantic's per-field titles so the TS generator inlines field types
    instead of emitting one alias per field (`Type1`, `Message1`, ...)."""
    if isinstance(node, dict):
        cleaned = {k: _strip_field_titles(v) for k, v in node.items()}
        if "properties" in cleaned:
            cleaned["properties"] = {
                name: {k: v for k, v in prop.items() if k != "title"}
                for name, prop in cleaned["properties"].items()
            }
        return cleaned
    if isinstance(node, list):
        return [_strip_field_titles(item) for item in node]
    return node


def build_schema() -> dict[str, Any]:
    events = _strip_field_titles(
        agent_event_adapter.json_schema(mode="serialization", ref_template="#/definitions/{model}")
    )
    request = _strip_field_titles(
        ChatRequest.model_json_schema(mode="validation", ref_template="#/definitions/{model}")
    )
    definitions: dict[str, Any] = {**events.pop("$defs", {}), **request.pop("$defs", {})}
    definitions["AgentEvent"] = events
    definitions["ChatRequest"] = request
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "Contracts",
        "description": "Generated from apps/api/app/contracts. Do not edit by hand.",
        "type": "object",
        "properties": {
            "event": {"$ref": "#/definitions/AgentEvent"},
            "chatRequest": {"$ref": "#/definitions/ChatRequest"},
        },
        "definitions": definitions,
    }


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(build_schema(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
