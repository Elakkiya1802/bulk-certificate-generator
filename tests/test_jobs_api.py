"""API tests: creating jobs, validation, status/progress, failures, retrieval."""

import io
import zipfile

from app.schemas import JobCreate
from app.services import job_service
from app.services.job_service import create_job, process_job


# ---------------------------------------------------------------- creating
def test_create_job_returns_202_and_job_details(client, make_payload):
    resp = client.post("/api/v1/jobs", json=make_payload(3))

    assert resp.status_code == 202
    body = resp.json()
    assert body["id"]
    assert body["total"] == 3
    assert body["course_title"] == "Python Backend Bootcamp"
    assert body["links"]["self"] == f"/api/v1/jobs/{body['id']}"


def test_job_is_processed_in_the_background(client, make_payload):
    job_id = client.post("/api/v1/jobs", json=make_payload(4)).json()["id"]

    status = client.get(f"/api/v1/jobs/{job_id}").json()
    assert status["status"] == "completed"
    assert status["succeeded"] == 4
    assert status["failed"] == 0
    assert status["progress_percent"] == 100.0
    assert status["completed_at"].endswith("Z")


# -------------------------------------------------------------- validation
def test_missing_required_job_fields_rejected(client, make_payload):
    payload = make_payload(1)
    del payload["course_title"]
    assert client.post("/api/v1/jobs", json=payload).status_code == 422


def test_empty_recipient_list_rejected(client, make_payload):
    assert client.post("/api/v1/jobs", json=make_payload(0)).status_code == 422


def test_too_many_recipients_rejected(client, make_payload):
    # the test settings allow at most 50
    resp = client.post("/api/v1/jobs", json=make_payload(51))
    assert resp.status_code == 422
    assert "Too many recipients" in resp.json()["detail"]


def test_invalid_recipients_are_reported_but_do_not_block_valid_ones(client, make_payload):
    payload = make_payload(
        0,
        recipients=[
            {"name": "Good One", "email": "good@example.com"},
            {"name": "Bad Email", "email": "not-an-email"},
            {"name": "   ", "email": "blank@example.com"},
            {"email": "noname@example.com"},
            "just a string",
            {"name": "Good Two", "email": "good2@example.com"},
        ],
    )
    resp = client.post("/api/v1/jobs", json=payload)
    assert resp.status_code == 202
    job_id = resp.json()["id"]

    status = client.get(f"/api/v1/jobs/{job_id}").json()
    assert status["status"] == "completed_with_errors"
    assert (status["succeeded"], status["failed"]) == (2, 4)

    failed = client.get(f"/api/v1/jobs/{job_id}/certificates", params={"status": "failed"}).json()
    assert [c["index"] for c in failed["items"]] == [1, 2, 3, 4]
    assert "email" in failed["items"][0]["error"]
    assert "name" in failed["items"][1]["error"]
    assert "name: Field required" in failed["items"][2]["error"]
    assert "object" in failed["items"][3]["error"]
    assert all(c["download_url"] is None for c in failed["items"])


def test_job_with_only_invalid_recipients_is_failed_immediately(client, make_payload):
    payload = make_payload(0, recipients=[{"name": "X", "email": "nope"}])
    body = client.post("/api/v1/jobs", json=payload).json()
    assert body["status"] == "failed"
    assert body["failed"] == 1
    assert body["succeeded"] == 0
    assert body["error_message"]


# --------------------------------------------------------- status/progress
def test_status_of_unknown_job_is_404(client):
    assert client.get("/api/v1/jobs/does-not-exist").status_code == 404


def test_pending_job_shows_zero_progress_then_completes(client, session_factory, settings, make_payload):
    # Create the job WITHOUT running the worker, to observe the "pending" state.
    with session_factory() as db:
        job = create_job(db, JobCreate(**make_payload(4)))
        job_id = job.id

    pending = client.get(f"/api/v1/jobs/{job_id}").json()
    assert pending["status"] == "pending"
    assert (pending["processed"], pending["pending"], pending["progress_percent"]) == (0, 4, 0.0)

    process_job(job_id, session_factory, settings.storage_dir)

    done = client.get(f"/api/v1/jobs/{job_id}").json()
    assert done["status"] == "completed"
    assert (done["processed"], done["pending"], done["progress_percent"]) == (4, 0, 100.0)


def test_progress_is_visible_while_the_job_is_running(client, session_factory, settings, make_payload, monkeypatch):
    with session_factory() as db:
        job_id = create_job(db, JobCreate(**make_payload(3))).id

    real_generate = job_service.generate_certificate_pdf
    seen = []

    def spying_generate(*args, **kwargs):
        # Called mid-job: ask the API how far along we are.
        body = client.get(f"/api/v1/jobs/{job_id}").json()
        seen.append((body["status"], body["processed"]))
        return real_generate(*args, **kwargs)

    monkeypatch.setattr(job_service, "generate_certificate_pdf", spying_generate)
    process_job(job_id, session_factory, settings.storage_dir)

    assert seen == [("processing", 0), ("processing", 1), ("processing", 2)]


# ------------------------------------------------ individual certificate failure
def test_one_failing_certificate_does_not_stop_the_others(client, make_payload, monkeypatch):
    real_generate = job_service.generate_certificate_pdf

    def flaky_generate(path, **kwargs):
        if kwargs["recipient_name"] == "Boom":
            raise RuntimeError("renderer exploded")
        return real_generate(path, **kwargs)

    monkeypatch.setattr(job_service, "generate_certificate_pdf", flaky_generate)

    payload = make_payload(
        0,
        recipients=[
            {"name": "Alice", "email": "a@example.com"},
            {"name": "Boom", "email": "b@example.com"},
            {"name": "Carol", "email": "c@example.com"},
        ],
    )
    job_id = client.post("/api/v1/jobs", json=payload).json()["id"]

    status = client.get(f"/api/v1/jobs/{job_id}").json()
    assert status["status"] == "completed_with_errors"
    assert (status["succeeded"], status["failed"], status["pending"]) == (2, 1, 0)

    items = client.get(f"/api/v1/jobs/{job_id}/certificates").json()["items"]
    by_name = {c["recipient_name"]: c for c in items}
    assert by_name["Alice"]["status"] == "success"
    assert by_name["Carol"]["status"] == "success"
    assert by_name["Boom"]["status"] == "failed"
    assert "renderer exploded" in by_name["Boom"]["error"]


def test_name_the_font_cannot_draw_fails_only_that_certificate(client, make_payload):
    payload = make_payload(
        0,
        recipients=[
            {"name": "Priya", "email": "p@example.com"},
            {"name": "ராஜ்", "email": "r@example.com"},
        ],
    )
    job_id = client.post("/api/v1/jobs", json=payload).json()["id"]

    status = client.get(f"/api/v1/jobs/{job_id}").json()
    assert (status["succeeded"], status["failed"]) == (1, 1)
    failed = client.get(f"/api/v1/jobs/{job_id}/certificates", params={"status": "failed"}).json()["items"][0]
    assert "not supported" in failed["error"]


def test_unexpected_worker_crash_marks_job_failed(client, session_factory, settings, make_payload, monkeypatch):
    with session_factory() as db:
        job_id = create_job(db, JobCreate(**make_payload(2))).id

    def broken_counts(*_a, **_k):
        raise RuntimeError("database went away")

    monkeypatch.setattr(job_service, "_status_counts", broken_counts)
    process_job(job_id, session_factory, settings.storage_dir)

    with session_factory() as db:
        from app.models import Job

        job = db.get(Job, job_id)
        assert job.status == "failed"
        assert "database went away" in job.error_message


# ---------------------------------------------------------------- retrieval
def _job_with_certs(client, make_payload, n=3):
    job_id = client.post("/api/v1/jobs", json=make_payload(n)).json()["id"]
    items = client.get(f"/api/v1/jobs/{job_id}/certificates").json()["items"]
    return job_id, items


def test_list_certificates_with_pagination_and_filter(client, make_payload):
    job_id, _ = _job_with_certs(client, make_payload, n=5)

    page = client.get(f"/api/v1/jobs/{job_id}/certificates", params={"limit": 2, "offset": 2}).json()
    assert page["total"] == 5
    assert [c["index"] for c in page["items"]] == [2, 3]

    none_failed = client.get(f"/api/v1/jobs/{job_id}/certificates", params={"status": "failed"}).json()
    assert none_failed["total"] == 0

    assert client.get(f"/api/v1/jobs/{job_id}/certificates", params={"status": "bogus"}).status_code == 422
    assert client.get("/api/v1/jobs/nope/certificates").status_code == 404


def test_download_single_certificate(client, make_payload):
    _, items = _job_with_certs(client, make_payload, n=2)

    resp = client.get(items[0]["download_url"])

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF")
    assert "certificate_Person_0.pdf" in resp.headers["content-disposition"]


def test_download_unknown_or_failed_certificate(client, make_payload):
    assert client.get("/api/v1/certificates/nope/download").status_code == 404

    payload = make_payload(0, recipients=[{"name": "X", "email": "bad"}, {"name": "Ok", "email": "ok@example.com"}])
    job_id = client.post("/api/v1/jobs", json=payload).json()["id"]
    failed = client.get(f"/api/v1/jobs/{job_id}/certificates", params={"status": "failed"}).json()["items"][0]

    resp = client.get(f"/api/v1/certificates/{failed['id']}/download")
    assert resp.status_code == 409


def test_deleted_file_returns_410(client, make_payload, settings):
    _, items = _job_with_certs(client, make_payload, n=1)
    for pdf in settings.storage_dir.rglob("*.pdf"):
        pdf.unlink()
    assert client.get(items[0]["download_url"]).status_code == 410


def test_download_all_as_zip_contains_only_successful_certificates(client, make_payload):
    payload = make_payload(
        0,
        recipients=[
            {"name": "Alice", "email": "a@example.com"},
            {"name": "Bad", "email": "invalid"},
            {"name": "Carol", "email": "c@example.com"},
        ],
    )
    job_id = client.post("/api/v1/jobs", json=payload).json()["id"]

    resp = client.get(f"/api/v1/jobs/{job_id}/download")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        assert sorted(zf.namelist()) == ["0000_Alice.pdf", "0002_Carol.pdf"]
        assert all(zf.read(n).startswith(b"%PDF") for n in zf.namelist())


def test_download_all_when_nothing_generated(client, make_payload):
    job_id = client.post(
        "/api/v1/jobs", json=make_payload(0, recipients=[{"name": "X", "email": "bad"}])
    ).json()["id"]
    assert client.get(f"/api/v1/jobs/{job_id}/download").status_code == 409
    assert client.get("/api/v1/jobs/nope/download").status_code == 404


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}
