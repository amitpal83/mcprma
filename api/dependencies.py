"""FastAPI dependency providers.

Kept separate from main.py so tests can override get_db via
app.dependency_overrides without touching route definitions.
"""
from __future__ import annotations

from collections.abc import Generator

from sqlalchemy.orm import Session

from db.session import SessionLocal


def get_db() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
