from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from app.core.config import get_settings

_engine: Engine | None = None


def get_engine() -> Engine:
    """Lazy engine so tests can point DATABASE_URL elsewhere before first use."""
    global _engine
    if _engine is None:
        _engine = create_engine(get_settings().sqlalchemy_url, pool_pre_ping=True)
    return _engine


def SessionLocal() -> Session:  # noqa: N802 — conventional sessionmaker-style name
    return Session(bind=get_engine(), autoflush=False, expire_on_commit=False)


def dispose_engine() -> None:
    """Test hook: drop the cached engine (e.g. after swapping DATABASE_URL)."""
    global _engine
    if _engine is not None:
        _engine.dispose()
        _engine = None


@contextmanager
def session_scope() -> Iterator[Session]:
    """Commit-on-success / rollback-on-error unit of work (worker + scripts)."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
