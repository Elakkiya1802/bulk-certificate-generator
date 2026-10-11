# Bulk Certificate Generator

A FastAPI backend that accepts **one request containing many recipients**, generates a PDF certificate for each valid recipient from a predefined template, lets the client **track progress**, and lets the client **download** the results (individually or as a ZIP).

- **Python 3.10+**, **FastAPI**, **SQLAlchemy 2** (SQLite by default, any relational DB via `DATABASE_URL`), **ReportLab** for PDFs, **pytest** for tests.

---

## 1. Setup

```bash
# from the project folder
python -m venv .venv

# activate it
source .venv/bin/activate          # macOS / Linux
.venv\Scripts\activate             # Windows (cmd / PowerShell)

pip install -r requirements-dev.txt
```

(`requirements.txt` has only the runtime dependencies; `requirements-dev.txt` adds the test tools.)

## 2. Run the application

```bash
uvicorn app.main:app --reload
```

- API: http://127.0.0.1:8000
- Interactive docs (try every endpoint in the browser): http://127.0.0.1:8000/docs

Tables are created automatically on startup. The SQLite database (`certificates.db`) and the generated PDFs (`storage/`) are created in the project folder.

Optional environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./certificates.db` | Any SQLAlchemy URL, e.g. `postgresql+psycopg2://user:pass@localhost/certs` |
| `CERT_STORAGE_DIR` | `./storage` | Where PDFs are saved |
| `MAX_RECIPIENTS` | `1000` | Maximum recipients per request |

## 3. Run the tests

```bash
pytest
```

Each test uses its own temporary database and storage folder, so tests never touch real data. They cover job creation, input validation, PDF generation, status/progress (including live progress mid-job), individual certificate failure, worker crash handling, and certificate retrieval.

## 4. Submit a certificate generation request

`POST /api/v1/jobs`

```bash
curl -X POST http://127.0.0.1:8000/api/v1/jobs \
  -H "Content-Type: application/json" \
  -d @sample_request.json
```

Request body:

```json
{
  "course_title": "Python Backend Bootcamp",
  "organization": "Acme Academy",
  "issue_date": "2026-10-11",
  "signatory_name": "Dr. A. Kumar",
  "recipients": [
    {"name": "Priya Sharma", "email": "priya@example.com"},
    {"name": "Arun Raj", "email": "arun@example.com"}
  ]
}
```

| Field | Required | Rules |
|---|---|---|
| `course_title` | yes | 1-150 chars |
| `organization` | yes | 1-100 chars |
| `issue_date` | no | `YYYY-MM-DD`, defaults to today |
| `signatory_name` | no | up to 100 chars |
| `recipients` | yes | 1 to `MAX_RECIPIENTS` items |
| `recipients[].name` | yes | 1-100 chars |
| `recipients[].email` | yes | valid email address |

The response is **`202 Accepted`** and returns immediately with the job (the PDFs are generated in the background):

```json
{
  "id": "4f573f3a-8996-40a2-971c-69b5c3f06a38",
  "status": "pending",
  "total": 5, "processed": 2, "succeeded": 0, "failed": 2, "pending": 3,
  "progress_percent": 40.0,
  "links": {
    "self": "/api/v1/jobs/4f573f3a-...",
    "certificates": "/api/v1/jobs/4f573f3a-.../certificates",
    "download_all": "/api/v1/jobs/4f573f3a-.../download"
  }
}
```

(Here 2 recipients were already rejected during validation, so `processed` is 2 even before the worker starts.)

### Check progress

`GET /api/v1/jobs/{job_id}`

```bash
curl http://127.0.0.1:8000/api/v1/jobs/<job_id>
```

Job `status` values: `pending` -> `processing` -> `completed` (all succeeded) / `completed_with_errors` (some failed) / `failed` (nothing generated).

### See which recipients succeeded or failed

`GET /api/v1/jobs/{job_id}/certificates?status=failed&limit=100&offset=0`

Each item has the recipient's `index` (position in your original request), `status`, an `error` message for failures, and a `download_url` for successes. `status` filter is optional (`pending`, `success`, `failed`).

## 5. Retrieve generated certificates

| What | Endpoint |
|---|---|
| One certificate (PDF) | `GET /api/v1/certificates/{certificate_id}/download` |
| All successful certificates (ZIP) | `GET /api/v1/jobs/{job_id}/download` |

```bash
curl -o certificates.zip http://127.0.0.1:8000/api/v1/jobs/<job_id>/download
```

The ZIP contains every certificate generated *so far*, so it also works while a job is still running. Error responses: `404` unknown id, `409` certificate not generated (failed/pending) or nothing to zip yet, `410` file missing from storage.

---

## Project structure

```
app/
  main.py               FastAPI app, startup (creates tables), /health
  config.py             settings from environment variables
  database.py           engine, session factory, FastAPI dependencies
  models.py             Job and Certificate tables, status enums
  schemas.py            request/response models + per-recipient rules
  routers/jobs.py       HTTP endpoints
  services/
    job_service.py      create job, validate recipients, background worker, progress
    pdf_generator.py    the single certificate template (ReportLab)
tests/                  pytest suite
scripts/make_bulk_request.py   builds a big request file for bulk demos
```

## Design decisions

**Asynchronous (background) processing.** The `POST` validates the input, saves the job and returns `202` right away; PDF generation runs afterwards in FastAPI's `BackgroundTasks` (in a worker thread, so the server keeps answering other requests). Reasoning: a bulk request can contain hundreds of recipients, and holding an HTTP connection open for all of them would risk client/proxy timeouts and a poor experience. The client polls the job instead. `BackgroundTasks` was chosen over Celery/Redis because it needs no extra infrastructure and keeps the project simple to run and review; the work is isolated in `process_job`, so moving it to a real queue later only changes how that function is invoked.

**Failure isolation.** Each certificate is generated inside its own `try/except` and its result is committed immediately. One bad certificate is marked `failed` with a readable error and the loop carries on. The job ends as `completed_with_errors` rather than failing as a whole.

**Two layers of validation.**
1. *Request level* (whole request is rejected with `422`): missing/invalid `course_title`, `organization`, `issue_date`, an empty `recipients` list, or more than `MAX_RECIPIENTS`.
2. *Recipient level* (request accepted, bad entries reported as failed): empty name, invalid email, missing field, or an entry that is not an object. Recipients are accepted as raw items and validated one by one on purpose; typing the field as `list[RecipientModel]` would make one typo reject all the other valid recipients.

**Progress is derived, not stored.** Counts are computed from the certificate rows (`GROUP BY status`), so they can never drift out of sync with the real data. The worker commits after every certificate, so progress is visible live.

**Storage.** PDFs are written to disk (`storage/<job_id>/<certificate_id>.pdf`); the database stores only the relative path. File names come from generated UUIDs, never from user input, which rules out path-traversal problems. PDFs are written to a temp file and renamed so a crash never leaves a corrupt certificate.

**Database.** SQLite by default for zero-setup; the code uses SQLAlchemy, so `DATABASE_URL` switches to PostgreSQL/MySQL. SQLite runs in WAL mode so clients can read progress while the worker writes.

**One template.** The certificate layout is code in `services/pdf_generator.py` (landscape A4, border, name, course, date, signatory, unique certificate ID).

## Known limitations (and what I would do next)

- **Server restart mid-job:** `BackgroundTasks` live in the process. If the server stops while a job is running, that job stays `processing`. Fix: a persistent queue (Celery/RQ) or a startup task that resumes unfinished jobs.
- **Fonts:** the built-in PDF fonts support Latin characters only. Names in other scripts (e.g. Tamil) fail *individually* with a clear error. Fix: register a Unicode TTF font in ReportLab.
- **No authentication / rate limiting**, and no duplicate-email detection; all would be added for production.
- **Local file storage:** for multiple servers, store PDFs in object storage such as S3.
- **Schema migrations:** tables are created with `create_all`; a real deployment would use Alembic.
