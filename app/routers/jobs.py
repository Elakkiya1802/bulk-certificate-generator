"""HTTP endpoints."""

import io
import re
import zipfile
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.database import get_db, get_session_factory
from app.models import Certificate, CertificateStatus, Job, JobStatus
from app.schemas import CertificateListOut, CertificateOut, JobCreate, JobLinks, JobOut
from app.services.job_service import create_job, job_progress, process_job

router = APIRouter(prefix="/api/v1", tags=["certificates"])


# ------------------------------------------------------------------ helpers
def _job_or_404(db: Session, job_id: str) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _job_out(db: Session, job: Job) -> JobOut:
    base = f"/api/v1/jobs/{job.id}"
    return JobOut(
        id=job.id,
        status=job.status,
        course_title=job.course_title,
        organization=job.organization,
        issue_date=job.issue_date,
        error_message=job.error_message,
        created_at=job.created_at,
        completed_at=job.completed_at,
        links=JobLinks(
            self=base,
            certificates=f"{base}/certificates",
            download_all=f"{base}/download",
        ),
        **job_progress(db, job),
    )


def _safe_filename(name: str | None) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", name or "").strip("_")
    return slug or "certificate"


def _certificate_out(cert: Certificate) -> CertificateOut:
    ok = cert.status == CertificateStatus.SUCCESS.value
    return CertificateOut(
        id=cert.id,
        index=cert.index,
        recipient_name=cert.recipient_name,
        recipient_email=cert.recipient_email,
        status=cert.status,
        error=cert.error,
        download_url=f"/api/v1/certificates/{cert.id}/download" if ok else None,
        generated_at=cert.generated_at,
    )


# ------------------------------------------------------------------- routes
@router.post(
    "/jobs",
    response_model=JobOut,
    status_code=202,
    summary="Submit a bulk certificate generation request",
)
def submit_job(
    payload: JobCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    session_factory: sessionmaker = Depends(get_session_factory),
    settings: Settings = Depends(get_settings),
):
    """Validates the request, stores the job and returns **202 Accepted** at once.

    The PDFs are generated in the background; poll `GET /jobs/{id}` for progress.
    Individual invalid recipients do not reject the request - they are reported
    as failed certificates in the job.
    """
    if len(payload.recipients) > settings.max_recipients:
        raise HTTPException(
            status_code=422,
            detail=f"Too many recipients: {len(payload.recipients)} (maximum is {settings.max_recipients})",
        )

    job = create_job(db, payload)
    if job.status == JobStatus.PENDING.value:
        background_tasks.add_task(process_job, job.id, session_factory, settings.storage_dir)
    return _job_out(db, job)


@router.get("/jobs/{job_id}", response_model=JobOut, summary="Job status and progress")
def get_job(job_id: str, db: Session = Depends(get_db)):
    return _job_out(db, _job_or_404(db, job_id))


@router.get(
    "/jobs/{job_id}/certificates",
    response_model=CertificateListOut,
    summary="List the certificates of a job (with per-certificate status and errors)",
)
def list_certificates(
    job_id: str,
    status: CertificateStatus | None = Query(default=None, description="Filter by status"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    _job_or_404(db, job_id)

    conditions = [Certificate.job_id == job_id]
    if status is not None:
        conditions.append(Certificate.status == status.value)

    total = db.scalar(select(func.count()).select_from(Certificate).where(*conditions))
    rows = db.scalars(
        select(Certificate).where(*conditions).order_by(Certificate.index).limit(limit).offset(offset)
    ).all()
    return CertificateListOut(
        job_id=job_id,
        total=total or 0,
        limit=limit,
        offset=offset,
        items=[_certificate_out(c) for c in rows],
    )


@router.get("/certificates/{certificate_id}/download", summary="Download one certificate (PDF)")
def download_certificate(
    certificate_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    cert = db.get(Certificate, certificate_id)
    if cert is None:
        raise HTTPException(status_code=404, detail="Certificate not found")
    if cert.status != CertificateStatus.SUCCESS.value or not cert.file_path:
        raise HTTPException(
            status_code=409,
            detail=f"Certificate is not available (status: {cert.status})",
        )

    path = Path(settings.storage_dir) / cert.file_path
    if not path.is_file():
        raise HTTPException(status_code=410, detail="Certificate file is no longer in storage")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=f"certificate_{_safe_filename(cert.recipient_name)}.pdf",
    )


@router.get("/jobs/{job_id}/download", summary="Download all generated certificates as a ZIP")
def download_job_zip(
    job_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Includes every certificate generated *so far*, so it also works while a
    job is still running or when some certificates failed."""
    _job_or_404(db, job_id)
    certs = db.scalars(
        select(Certificate)
        .where(
            Certificate.job_id == job_id,
            Certificate.status == CertificateStatus.SUCCESS.value,
        )
        .order_by(Certificate.index)
    ).all()

    buffer = io.BytesIO()
    added = 0
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for cert in certs:
            path = Path(settings.storage_dir) / (cert.file_path or "")
            if path.is_file():
                zf.write(path, arcname=f"{cert.index:04d}_{_safe_filename(cert.recipient_name)}.pdf")
                added += 1

    if added == 0:
        raise HTTPException(status_code=409, detail="No certificates have been generated for this job yet")

    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="certificates_{job_id}.zip"'},
    )
