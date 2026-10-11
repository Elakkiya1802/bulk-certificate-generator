"""Renders ONE certificate PDF from the single predefined template.

The template is code: a landscape A4 page with a double border, centred text and
a footer (issue date, signatory, certificate ID). It only knows how to draw one
certificate and has no knowledge of jobs or the database, which makes it easy to
test and to swap for another library later.
"""

import os
from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

PAGE_WIDTH, PAGE_HEIGHT = landscape(A4)
NAVY = colors.HexColor("#1F3A5F")
GOLD = colors.HexColor("#B8860B")
GREY = colors.HexColor("#666666")


class UnsupportedCharacterError(ValueError):
    """Text contains characters the built-in PDF font cannot draw."""


def _ensure_supported(text: str, label: str) -> None:
    # The built-in Helvetica/Times fonts use the Windows-1252 (Latin) character
    # set. Failing loudly beats silently printing garbage on a certificate.
    try:
        text.encode("cp1252")
    except UnicodeEncodeError as exc:
        raise UnsupportedCharacterError(
            f"{label} contains characters not supported by the certificate font"
        ) from exc


def _fit_font_size(text: str, font: str, max_size: int, min_size: int, max_width: float) -> int:
    size = max_size
    while size > min_size and stringWidth(text, font, size) > max_width:
        size -= 1
    return size


def generate_certificate_pdf(
    output_path: Path,
    *,
    certificate_id: str,
    recipient_name: str,
    course_title: str,
    organization: str,
    issue_date: date,
    signatory_name: str | None = None,
) -> Path:
    """Write the certificate to ``output_path`` and return the path.

    The PDF is written to a temporary file first and renamed at the end, so a
    crash can never leave a half-written certificate that looks valid.
    """
    for label, value in (
        ("Recipient name", recipient_name),
        ("Course title", course_title),
        ("Organization", organization),
        ("Signatory name", signatory_name or ""),
    ):
        _ensure_supported(value, label)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_name(output_path.name + ".tmp")

    try:
        c = canvas.Canvas(str(tmp_path), pagesize=(PAGE_WIDTH, PAGE_HEIGHT))
        c.setTitle(f"Certificate - {recipient_name}")
        c.setAuthor(organization)
        centre = PAGE_WIDTH / 2

        # Double border
        c.setStrokeColor(NAVY)
        c.setLineWidth(4)
        c.rect(20, 20, PAGE_WIDTH - 40, PAGE_HEIGHT - 40)
        c.setStrokeColor(GOLD)
        c.setLineWidth(1)
        c.rect(30, 30, PAGE_WIDTH - 60, PAGE_HEIGHT - 60)

        # Header
        c.setFillColor(NAVY)
        c.setFont("Helvetica-Bold", 16)
        c.drawCentredString(centre, PAGE_HEIGHT - 90, organization.upper())

        c.setFont("Times-Bold", 46)
        c.drawCentredString(centre, PAGE_HEIGHT - 150, "CERTIFICATE")
        c.setFillColor(GOLD)
        c.setFont("Helvetica", 16)
        c.drawCentredString(centre, PAGE_HEIGHT - 178, "OF COMPLETION")

        # Recipient
        c.setFillColor(GREY)
        c.setFont("Helvetica-Oblique", 14)
        c.drawCentredString(centre, PAGE_HEIGHT - 225, "This is to certify that")

        name_font = "Times-BoldItalic"
        name_size = _fit_font_size(recipient_name, name_font, 40, 10, PAGE_WIDTH - 160)
        c.setFillColor(NAVY)
        c.setFont(name_font, name_size)
        c.drawCentredString(centre, PAGE_HEIGHT - 275, recipient_name)
        name_width = stringWidth(recipient_name, name_font, name_size)
        c.setStrokeColor(GOLD)
        c.setLineWidth(1)
        c.line(centre - name_width / 2 - 20, PAGE_HEIGHT - 285, centre + name_width / 2 + 20, PAGE_HEIGHT - 285)

        # Course
        c.setFillColor(GREY)
        c.setFont("Helvetica", 14)
        c.drawCentredString(centre, PAGE_HEIGHT - 320, "has successfully completed")

        c.setFillColor(NAVY)
        c.setFont("Helvetica-Bold", 24)
        y = PAGE_HEIGHT - 358
        for line in simpleSplit(course_title, "Helvetica-Bold", 24, PAGE_WIDTH - 200):
            c.drawCentredString(centre, y, line)
            y -= 30

        # Footer: date (left), signatory (right)
        footer_y = 120
        c.setStrokeColor(GREY)
        c.setLineWidth(0.8)
        c.line(110, footer_y, 290, footer_y)
        c.setFillColor(NAVY)
        c.setFont("Helvetica-Bold", 12)
        c.drawCentredString(200, footer_y + 8, f"{issue_date.day} {issue_date.strftime('%B %Y')}")
        c.setFillColor(GREY)
        c.setFont("Helvetica", 10)
        c.drawCentredString(200, footer_y - 15, "Date of Issue")

        sig_x = PAGE_WIDTH - 200
        c.line(sig_x - 90, footer_y, sig_x + 90, footer_y)
        if signatory_name:
            c.setFillColor(NAVY)
            c.setFont("Times-Italic", 14)
            c.drawCentredString(sig_x, footer_y + 8, signatory_name)
        c.setFillColor(GREY)
        c.setFont("Helvetica", 10)
        c.drawCentredString(sig_x, footer_y - 15, "Authorized Signatory")

        # Unique ID so a certificate can be traced back to its record
        c.setFont("Helvetica", 8)
        c.drawCentredString(centre, 45, f"Certificate ID: {certificate_id}")

        c.showPage()
        c.save()
        os.replace(tmp_path, output_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    return output_path
