"""Shared fixtures: every test gets its own throw-away database and storage folder."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app import models  # noqa: F401  (registers tables)
from app.config import Settings, get_settings
from app.database import Base, get_db, get_session_factory, make_engine
from app.main import app


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        storage_dir=tmp_path / "storage",
        max_recipients=50,
    )


@pytest.fixture
def session_factory(settings):
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


@pytest.fixture
def client(settings, session_factory):
    def override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_session_factory] = lambda: session_factory
    app.dependency_overrides[get_settings] = lambda: settings
    # NOTE: no `with` block -> startup hooks (which touch the real DB) are not run.
    # FastAPI BackgroundTasks still run to completion before the response
    # returns to TestClient, so a job is finished when the POST call comes back.
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def make_payload():
    def _make(n: int = 3, **overrides):
        payload = {
            "course_title": "Python Backend Bootcamp",
            "organization": "Acme Academy",
            "issue_date": "2026-10-11",
            "signatory_name": "Dr. A. Kumar",
            "recipients": [
                {"name": f"Person {i}", "email": f"person{i}@example.com"} for i in range(n)
            ],
        }
        payload.update(overrides)
        return payload

    return _make
