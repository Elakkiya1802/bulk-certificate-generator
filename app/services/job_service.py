"""Business logic: creating jobs, processing them, and computing progress."""

import logging
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.models import Certificate, CertificateStatus, Job, JobStatus, utcnow
from app.schemas import JobCreate, RecipientData
from app.services.pdf_generator import generate_certificate_pdf

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------
def _format_validation_error(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        field = ".".join(str(p) for p in err["loc"]) or "recipient"
        parts.append(f"{field}: {err['msg']}")
    return "; ".join(parts)


def validate_recipient(raw: Any) -> tuple[RecipientData | None, str | None]:
    """Return (recipient, None) if valid, otherwise (None, human-readable error)."""
    if not isinstance(raw, dict):
        return None, "Recipient must be an object with 'name' and 'email'"
    try:
        return RecipientData.model_validate(raw), None
    except ValidationError as exc:
        return None, _format_validation_error(exc)


def _raw_text(raw: Any, key: str) -> str | None:
    """Best-effort copy of what the client sent, so failures are easy to trace."""
    if isinstance(raw, dict) and raw.get(key) is not None:
        return str(raw[key])[:255]
    return None


# --------------------------------------------------------------------------
# Creating a job (fast: validation + DB inserts only, no PDF work)
# --------------------------------------------------------------------------
def create_job(db: Session, payload: JobCreate) -> Job:
    job = Job(
        course_title=payload.course_title,
        organization=payload.organization,
        issue_date=payload.issue_date,
        signatory_name=payload.signatory_name,
        total_count=len(payload.recipients),
        status=JobStatus.PENDING.value,
    )
    db.add(job)

    valid_count = 0
    for index, raw in enumerate(payload.recipients):
        recipient, error = validate_recipient(raw)
        if recipient is None:
            # Invalid recipients are recorded as failed immediately. They never
            # block the valid ones.
            cert = Certificate(
                job=job,
                index=index,
                recipient_name=_raw_text(raw, "name"),
                recipient_email=_raw_text(raw, "email"),
                status=CertificateStatus.FAILED.value,
                error=error,
            )
        else:
            valid_count += 1
            cert = Certificate(
                job=job,
                index=index,
                recipient_name=recipient.name,
                recipient_email=str(recipient.email),
                status=CertificateStatus.PENDING.value,
            )
        db.add(cert)

    if valid_count == 0:
        # Nothing to generate, so the job is already finished.
        job.status = JobStatus.FAILED.value
        job.completed_at = utcnow()
        job.error_message = "No valid recipients in the request"

    db.commit()
    return job


# --------------------------------------------------------------------------
# Processing a job (slow: runs in the background)
# --------------------------------------------------------------------------
def _status_counts(db: Session, job_id: str) -> dict[str, int]:
    rows = db.execute(
        select(Certificate.status, func.count())
        .where(Certificate.job_id == job_id)
        .group_by(Certificate.status)
    ).all()
    counts = {s.value: 0 for s in CertificateStatus}
    counts.update({status: count for status, count in rows})
    return counts


def _generate_one(cert: Certificate, job: Job, storage_dir: Path) -> None:
    """Generate one certificate. Never raises: a failure is recorded on the row."""
    relative_path = f"{job.id}/{cert.id}.pdf"
    try:
        generate_certificate_pdf(
            storage_dir / relative_path,
            certificate_id=cert.id,
            recipient_name=cert.recipient_name,
            course_title=job.course_title,
            organization=job.organization,
            issue_date=job.issue_date,
            signatory_name=job.signatory_name,
        )
    except Exception as exc:  # noqa: BLE001 - one bad certificate must not stop the job
        logger.warning("Certificate %s failed: %s", cert.id, exc)
        cert.status = CertificateStatus.FAILED.value
        cert.error = str(exc)[:500] or type(exc).__name__
        cert.file_path = None
    else:
        cert.status = CertificateStatus.SUCCESS.value
        cert.file_path = relative_path
        cert.error = None
        cert.generated_at = utcnow()


def _final_status(counts: dict[str, int]) -> JobStatus:
    total = sum(counts.values())
    if counts[CertificateStatus.FAILED.value] == total:
        return JobStatus.FAILED
    if counts[CertificateStatus.FAILED.value] == 0:
        return JobStatus.COMPLETED
    return JobStatus.COMPLETED_WITH_ERRORS


def process_job(job_id: str, session_factory: sessionmaker, storage_dir: Path) -> None:
    """Background worker. Opens its OWN database session (the request's session
    is closed as soon as the HTTP response is sent) and commits after every
    certificate so clients can watch progress in real time."""
    storage_dir = Path(storage_dir)
    with session_factory() as db:
        job = db.get(Job, job_id)
        if job is None:
            logger.error("process_job: job %s not found", job_id)
            return

        try:
            job.status = JobStatus.PROCESSING.value
            db.commit()

            pending = db.scalars(
                select(Certificate)
                .where(
                    Certificate.job_id == job_id,
                    Certificate.status == CertificateStatus.PENDING.value,
                )
                .order_by(Certificate.index)
            ).all()

            for cert in pending:
                _generate_one(cert, job, storage_dir)
                db.commit()  # progress becomes visible to GET /jobs/{id}

            job.status = _final_status(_status_counts(db, job_id)).value
            job.completed_at = utcnow()
            db.commit()
        except Exception as exc:  # noqa: BLE001
            # Only reached for unexpected infrastructure problems (e.g. the
            # database going away), never for a single bad certificate.
            logger.exception("Job %s crashed", job_id)
            db.rollback()
            job = db.get(Job, job_id)
            job.status = JobStatus.FAILED.value
            job.error_message = f"Unexpected error: {exc}"[:500]
            job.completed_at = utcnow()
            db.commit()


# --------------------------------------------------------------------------
# Progress
# --------------------------------------------------------------------------
def job_progress(db: Session, job: Job) -> dict[str, Any]:
    counts = _status_counts(db, job.id)
    succeeded = counts[CertificateStatus.SUCCESS.value]
    failed = counts[CertificateStatus.FAILED.value]
    processed = succeeded + failed
    total = job.total_count
    return {
        "total": total,
        "processed": processed,
        "succeeded": succeeded,
        "failed": failed,
        "pending": total - processed,
        "progress_percent": round(processed / total * 100, 1) if total else 100.0,
    }
