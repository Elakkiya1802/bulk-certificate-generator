"""Database engine, session factory and FastAPI dependencies."""

from collections.abc import Iterator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


def make_engine(url: str) -> Engine:
    kwargs: dict = {}
    is_sqlite = url.startswith("sqlite")
    if is_sqlite:
        # The background worker runs in a different thread than the request
        # handler, so SQLite must allow cross-thread use. A generous timeout
        # avoids "database is locked" errors while a job is writing progress.
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}

    engine = create_engine(url, **kwargs)

    if is_sqlite:

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _record):
            cursor = dbapi_connection.cursor()
            # WAL lets clients poll job status while the worker is writing.
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


engine = make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    """Create tables if they do not exist (simple alternative to migrations)."""
    from app import models  # noqa: F401  (registers the models on Base)

    Base.metadata.create_all(bind=engine)


def get_session_factory() -> sessionmaker:
    """Dependency: the factory the background worker uses to open its OWN session."""
    return SessionLocal


def get_db() -> Iterator[Session]:
    """Dependency: one session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
