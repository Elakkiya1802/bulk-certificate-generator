"""Certificate generation: the PDF template itself."""

from datetime import date

import pytest
from pypdf import PdfReader

from app.services.pdf_generator import UnsupportedCharacterError, generate_certificate_pdf


def _make(path, **overrides):
    args = dict(
        certificate_id="abc-123",
        recipient_name="Priya Sharma",
        course_title="Python Backend Bootcamp",
        organization="Acme Academy",
        issue_date=date(2026, 10, 11),
        signatory_name="Dr. A. Kumar",
    )
    args.update(overrides)
    return generate_certificate_pdf(path, **args)


def test_generates_valid_pdf_with_recipient_details(tmp_path):
    out = _make(tmp_path / "nested" / "cert.pdf")

    assert out.exists()
    assert out.read_bytes().startswith(b"%PDF")
    reader = PdfReader(str(out))
    assert len(reader.pages) == 1
    text = reader.pages[0].extract_text()
    for expected in ("Priya Sharma", "Python Backend Bootcamp", "ACME ACADEMY", "11 October 2026", "abc-123"):
        assert expected in text


def test_signatory_is_optional(tmp_path):
    out = _make(tmp_path / "cert.pdf", signatory_name=None)
    assert out.read_bytes().startswith(b"%PDF")


def test_very_long_name_and_title_still_render(tmp_path):
    out = _make(
        tmp_path / "cert.pdf",
        recipient_name="A" * 100,
        course_title="An Extremely Long Course Title " * 4,
    )
    assert out.exists()


def test_unsupported_characters_raise_and_leave_no_file(tmp_path):
    target = tmp_path / "cert.pdf"
    with pytest.raises(UnsupportedCharacterError):
        _make(target, recipient_name="ராஜ்")
    assert not target.exists()
    assert not list(tmp_path.glob("*.tmp"))
