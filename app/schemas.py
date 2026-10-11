"""Request / response schemas (Pydantic)."""

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_serializer


# --------------------------------------------------------------------------
# Requests
# --------------------------------------------------------------------------
class RecipientData(BaseModel):
    """Rules every single recipient must satisfy.

    This is NOT used as the request field type on purpose: if it were, one bad
    recipient would make FastAPI reject the WHOLE request with a 422. Instead the
    request accepts raw objects and each one is validated individually, so a bad
    entry is reported as a failed certificate while the good ones still succeed.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=100)
    email: EmailStr


class JobCreate(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        json_schema_extra={
            "examples": [
                {
                    "course_title": "Python Backend Bootcamp",
                    "organization": "Acme Academy",
                    "issue_date": "2026-10-11",
                    "signatory_name": "Dr. A. Kumar",
                    "recipients": [
                        {"name": "Priya Sharma", "email": "priya@example.com"},
                        {"name": "Arun Raj", "email": "arun@example.com"},
                    ],
                }
            ]
        },
    )

    course_title: str = Field(min_length=1, max_length=150)
    organization: str = Field(min_length=1, max_length=100)
    issue_date: date = Field(default_factory=date.today)
    signatory_name: str | None = Field(default=None, max_length=100)
    # Raw items; each one is validated individually (see RecipientData).
    # The maximum size is enforced in the route using the configured limit.
    recipients: list[Any] = Field(min_length=1)


# --------------------------------------------------------------------------
# Responses
# --------------------------------------------------------------------------
class _UtcModel(BaseModel):
    """Serialises datetimes as ISO-8601 UTC with a trailing 'Z'."""

    @field_serializer("created_at", "completed_at", "generated_at", check_fields=False)
    def _serialize_dt(self, value: datetime | None) -> str | None:
        return value.isoformat() + "Z" if value else None


class JobLinks(BaseModel):
    self: str
    certificates: str
    download_all: str


class JobOut(_UtcModel):
    id: str
    status: str
    course_title: str
    organization: str
    issue_date: date
    total: int
    processed: int  # succeeded + failed
    succeeded: int
    failed: int
    pending: int
    progress_percent: float
    error_message: str | None = None
    created_at: datetime
    completed_at: datetime | None = None
    links: JobLinks


class CertificateOut(_UtcModel):
    id: str
    index: int
    recipient_name: str | None
    recipient_email: str | None
    status: str
    error: str | None = None
    download_url: str | None = None
    generated_at: datetime | None = None


class CertificateListOut(BaseModel):
    job_id: str
    total: int  # number of items matching the filter
    limit: int
    offset: int
    items: list[CertificateOut]
