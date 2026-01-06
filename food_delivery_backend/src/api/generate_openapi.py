"""
Generate and persist the FastAPI OpenAPI schema to interfaces/openapi.json.

Run from the backend container root:

    python -m src.api.generate_openapi

This script imports the FastAPI `app` and writes `app.openapi()` output to the
`interfaces/openapi.json` file for consumption by other containers (e.g., frontend
API client generation).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from src.api.main import app


# PUBLIC_INTERFACE
def generate_openapi(output_path: str | os.PathLike = "interfaces/openapi.json") -> Path:
    """Generate OpenAPI JSON from the FastAPI `app` and write it to disk."""
    schema = app.openapi()

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(schema, indent=2), encoding="utf-8")
    return out_path


if __name__ == "__main__":
    generate_openapi()
