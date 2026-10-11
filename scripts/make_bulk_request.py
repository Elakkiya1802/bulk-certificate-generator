"""Generate a large request file to try the bulk behaviour.

    python scripts/make_bulk_request.py 500 > bulk_request.json
    curl -X POST localhost:8000/api/v1/jobs -H "Content-Type: application/json" -d @bulk_request.json
"""

import json
import sys

count = int(sys.argv[1]) if len(sys.argv) > 1 else 200
recipients = [{"name": f"Participant {i}", "email": f"participant{i}@example.com"} for i in range(1, count + 1)]
# Sprinkle in a few invalid ones to show partial failures.
if count >= 3:
    recipients[1]["email"] = "broken-email"
    recipients[2]["name"] = ""

print(
    json.dumps(
        {
            "course_title": "Python Backend Bootcamp",
            "organization": "Acme Academy",
            "issue_date": "2026-10-11",
            "signatory_name": "Dr. A. Kumar",
            "recipients": recipients,
        },
        indent=2,
    )
)
