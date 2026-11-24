"""Send kb/*.md to a deployed API's /admin/ingest endpoint.

    API_URL=https://... ADMIN_TOKEN=... uv run python -m scripts.push_kb

The API chunks, embeds and stores the documents with its own credentials.
"""

import os
from pathlib import Path

import httpx

KB_DIR = Path(__file__).resolve().parents[3] / "kb"


def main() -> None:
    api_url = os.environ["API_URL"].rstrip("/")
    token = os.environ["ADMIN_TOKEN"]
    documents = [
        {"name": path.name, "content": path.read_text(encoding="utf-8")}
        for path in sorted(KB_DIR.glob("*.md"))
        if path.name != "README.md"
    ]
    response = httpx.post(
        f"{api_url}/admin/ingest",
        json={"documents": documents},
        headers={"Authorization": f"Bearer {token}"},
        timeout=120,
    )
    response.raise_for_status()
    print(response.json())


if __name__ == "__main__":
    main()
