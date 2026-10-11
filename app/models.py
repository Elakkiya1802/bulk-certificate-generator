"""Database models: one Job has many Certificates."""

import enum
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    # Stored as naive UTC so behaviour is identical on SQLite and PostgreSQL.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_id() -> str:
    return str(uuid.uuid4())


class JobStatus(str, enum.Enum):
    PENDING = "pending"  # accepted, waiting for the worker
    PROCESSING = "processing"  # worker is generating certificates
    COMPLETED = "completed"  # every certificate succeeded
    COMPLETED_WITH_ERRORS = "completed_with_errors"  # some succeeded, some failed
    FAILED = "failed"  # nothing could be generated


class CertificateStatus(str, enum.Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)

    # Job-level certificate information (shared by every recipient in the job)
    course_title: Mapped[str] = mapped_column(String(150))
    organization: Mapped[str] = mapped_column(String(100))
    issue_date: Mapped[date] = mapped_column(Date)
    signatory_name: Mapped[str | None] = mapped_column(String(100), nullable=True)

    status: Mapped[str] = mapped_column(String(30), default=JobStatus.PENDING.value, index=True)
    total_count: Mapped[int] = mapped_column(Integer)
    # Only set if the worker itself crashed (not for individual certificate failures)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    certificates: Mapped[list["Certificate"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="Certificate.index"
    )


class Certificate(Base):
    __tablename__ = "certificates"
    __table_args__ = (
        UniqueConstraint("job_id", "index", name="uq_certificate_job_index"),
        Index("ix_certificates_job_status", "job_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)

    # Position of the recipient in the original request (0-based), so a client
    # can map a failure back to the exact entry it submitted.
    index: Mapped[int] = mapped_column(Integer)

    # Stored exactly as received (may be missing/invalid for rejected recipients)
    recipient_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    recipient_email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    status: Mapped[str] = mapped_column(String(20), default=CertificateStatus.PENDING.value)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Path relative to the storage directory, e.g. "<job_id>/<certificate_id>.pdf"
    file_path: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    job: Mapped[Job] = relationship(back_populates="certificates")
