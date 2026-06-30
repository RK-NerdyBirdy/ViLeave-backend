# Medical Leave Platform — Backend

FastAPI backend for a college medical leave management system.

## Stack
- **Framework:** FastAPI (async)
- **Database:** PostgreSQL via SQLAlchemy 2.0 (asyncpg) + Alembic
- **Storage:** Cloudflare R2 (S3-compatible) for PDF medical certificates
- **Auth:** Google OAuth → server-issued JWT (with DB-backed revocation/blocklist)
- **Email:** Gmail SMTP via `aiosmtplib` (async, non-blocking)

## Setup

### 1. Install dependencies
```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment
```bash
cp .env.example .env
# Edit .env with real values:
#   - DATABASE_URL / SYNC_DATABASE_URL (Postgres connection strings)
#   - JWT_SECRET_KEY (generate with: openssl rand -hex 32)
#   - GOOGLE_CLIENT_ID (from Google Cloud Console OAuth credentials)
#   - R2_* (from Cloudflare R2 dashboard → Manage API Tokens)
#   - SMTP_USERNAME / SMTP_PASSWORD (Gmail App Password, NOT your account password —
#     generate one at https://myaccount.google.com/apppasswords)
```

### 3. Create the database
```bash
createdb medical_leave_db
```

### 4. Run migrations
```bash
# Apply all migrations (creates all 6 tables)
alembic upgrade head

# To create a NEW migration after changing models:
alembic revision --autogenerate -m "describe your change"
alembic upgrade head

# To roll back the last migration:
alembic downgrade -1

# To preview the SQL without running it:
alembic upgrade head --sql
```

### 5. Bootstrap the first HOD account
The API has no way to create the first HOD via HTTP (chicken-and-egg: all
`/hod/*` endpoints require an authenticated HOD). Run this once:

```bash
python -m scripts.bootstrap_hod \
    --email hod@college.edu \
    --name "Jane Smith" \
    --faculty-id 00001 \
    --phone "+911234567890" \
    --designation "Head of Department" \
    --honorific "Dr." \
    --max-leave-days 7
```

This person can now sign in via Google OAuth using `hod@college.edu` and
will have full HOD privileges — including adding all other students and
faculty via `POST /hod/students`, `POST /hod/faculty`, or the bulk CSV
endpoints.

### 6. Run the server
```bash
uvicorn app.main:app --reload --port 8000
```

Interactive API docs: `http://localhost:8000/docs`

## Project Structure
```
app/
├── main.py                 # App factory, lifespan, middleware, routers
├── config.py                # Pydantic Settings (env vars)
├── database.py               # Async engine + session factory
├── dependencies.py            # Auth dependencies (get_current_user, require_*)
├── models/                    # SQLAlchemy ORM models
├── schemas/                   # Pydantic request/response models
├── routers/                   # HTTP route handlers (thin — delegate to services)
├── services/                  # All business logic
│   ├── auth_service.py         # Google OAuth verification + JWT
│   ├── token_blocklist.py      # JWT revocation on logout
│   ├── leave_service.py        # Leave request state machine
│   ├── user_service.py         # Student/Faculty CRUD + CSV bulk ingest
│   ├── storage_service.py      # Cloudflare R2 (boto3)
│   ├── email_service.py        # Gmail SMTP notifications
│   └── export_service.py       # ZIP export with PDFs + manifest
└── utils/
    ├── validators.py            # Format validators (reg_no, faculty_id)
    └── exceptions.py            # HTTP exception factories

alembic/
├── env.py                      # Async-aware migration environment
└── versions/0001_initial_schema.py

scripts/
└── bootstrap_hod.py             # One-time first-HOD creation script
```

## Key Design Notes

**Proctor snapshot:** `leave_requests.assigned_proctor_id` is captured at
submission time and never changes, even if the student's proctor is later
reassigned. This preserves an accurate audit trail of who actually reviewed
each request.

**Soft delete:** Leave requests are never hard-deleted. `is_deleted` +
`deleted_at` preserve the audit trail. On HOD rejection, the PDF is
hard-deleted from R2 (irreversible) but the DB row survives in a redacted
form (`pdf_r2_key`, `proctor_remarks`, `hod_remarks` all set to `NULL`).

**Token revocation:** JWTs are stateless by default, but `POST /auth/logout`
adds the token's `jti` to a DB-backed blocklist (with an in-memory cache for
O(1) lookups on every request). A background task prunes expired entries
hourly so the table never grows unboundedly.

**CSV bulk ingest:** Column headers are validated strictly — the entire file
is rejected if any required column is missing. Individual row failures
(bad format, duplicate email, unknown proctor) don't abort the whole import;
they're reported in the response's `errors` array.

**Export ZIP:** `GET /hod/export/leave-requests` streams a ZIP built
entirely in memory (`io.BytesIO`), containing a `manifest.csv` plus a
`pdfs/` folder with files named
`{registration_no}_{student_name}_{start_date}_to_{end_date}.pdf`. Filenames
are sanitised to strip path-traversal characters and unsafe symbols.

## Testing the bootstrap → login flow locally without real Google OAuth

For local development without setting up real Google OAuth credentials, you
can mint a JWT directly for testing:

```python
from app.services.auth_service import create_access_token
from app.models.user import UserRole
import uuid

token = create_access_token(
    user_id=uuid.UUID("<hod-user-id-from-bootstrap-output>"),
    role=UserRole.HOD,
    email="hod@college.edu",
)
print(token)
```

Use this token in `Authorization: Bearer <token>` to test HOD endpoints via
`/docs` before wiring up the real frontend OAuth flow.
