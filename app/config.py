"""Application settings.

Everything configurable lives here and can be overridden with environment
variables, so the same code runs locally (SQLite) and in production
(e.g. PostgreSQL) without changes.
"""

import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    # Any SQLAlchemy URL works, e.g. postgresql+psycopg2://user:pass@localhost/certs
    database_url: str = f"sqlite:///{BASE_DIR / 'certificates.db'}"
    # Where generated PDFs are stored on disk.
    storage_dir: Path = BASE_DIR / "storage"
    # Upper bound on recipients per request (protects the server from huge payloads).
    max_recipients: int = 1000

    @classmethod
    def from_env(cls) -> "Settings":
        defaults = cls()
        return cls(
            database_url=os.getenv("DATABASE_URL", defaults.database_url),
            storage_dir=Path(os.getenv("CERT_STORAGE_DIR", str(defaults.storage_dir))),
            max_recipients=int(os.getenv("MAX_RECIPIENTS", defaults.max_recipients)),
        )


settings = Settings.from_env()


def get_settings() -> Settings:
    """FastAPI dependency (tests override this to use temp folders)."""
    return settings
