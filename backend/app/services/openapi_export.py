"""The API's OpenAPI schema as a deterministic JSON document (backend plan step 20, OPS-05).

`backend/openapi.json` is committed. The frontend generates its types from that file, so CI can
check both halves without a running server: a backend test compares the file with this export, and
the frontend job regenerates `schema.d.ts` from it and fails on any diff.
"""

import json

from app.config import Settings
from app.main import create_app


def openapi_document() -> str:
    """Indented JSON with a trailing newline. Reads no `.env` and needs no database or network."""
    app = create_app(Settings(_env_file=None, app_env="test"))
    return json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n"
