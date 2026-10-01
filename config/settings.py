"""Central configuration for the RMA project.

No secrets or credentials belong in this file — it is committed/shared as
plain project configuration. Anything sensitive (API keys, tokens) must be
supplied via environment variables and read here with os.environ, never
hardcoded.
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"

DATA_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)

DATABASE_PATH = DATA_DIR / "rma.db"

# Allows tests / alternate environments to point at a different database
# without changing code.
DATABASE_URL = os.environ.get("RMA_DATABASE_URL", f"sqlite:///{DATABASE_PATH}")

LOG_FILE = LOG_DIR / "app.log"
