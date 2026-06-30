"""
app/services/export_service.py
───────────────────────────────
Builds a ZIP archive for the HOD export endpoint.

ZIP structure:
    medical_leave_export_<YYYYMMDD_HHMMSS>/
    ├── manifest.csv
    └── pdfs/
        ├── 24CSE0001_John_Doe_2024-11-01_to_2024-11-03.pdf
        └── 24CSE0042_Jane_Smith_2024-11-05_to_2024-11-07.pdf

Design notes:
  - The ZIP is built fully in-memory using io.BytesIO so nothing touches
    disk and the StreamingResponse can pipe it directly to the client.
  - PDFs are fetched from R2 one at a time using boto3 get_object to avoid
    loading all files into memory simultaneously for large exports.
  - Requests whose PDFs have been deleted (HOD-rejected / student-withdrawn)
    are listed in the manifest with status "NO_PDF" and skipped in pdfs/.
  - The manifest CSV columns match exactly what an admin would need for
    record-keeping.
"""
import csv
import io
import re
import zipfile
from datetime import date, datetime, timezone
from typing import AsyncGenerator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.leave_request import LeaveRequest, LeaveStatus
from app.models.user import Faculty, Student, User
from app.services.storage_service import _get_s3_client, StorageError
from app.config import get_settings

settings = get_settings()


# ── Filename sanitiser ────────────────────────────────────────────────────────

def _safe_name(value: str) -> str:
    """
    Replace any character that is unsafe in a filename with an underscore.
    Collapses multiple consecutive underscores into one.
    """
    sanitised = re.sub(r"[^\w\-.]", "_", value)
    return re.sub(r"_+", "_", sanitised).strip("_")


def _pdf_filename(reg_no: str, full_name: str, start: date, end: date) -> str:
    """
    Build a human-readable, filesystem-safe PDF filename.
    Example: 24CSE0001_John_Doe_2024-11-01_to_2024-11-03.pdf
    """
    name_part = _safe_name(full_name.replace(" ", "_"))
    return f"{reg_no}_{name_part}_{start}_to_{end}.pdf"


# ── R2 direct download ────────────────────────────────────────────────────────

def _download_pdf_bytes(r2_key: str) -> bytes:
    """
    Download a PDF from R2 and return its raw bytes.
    Uses get_object (streaming) rather than presigned URL to keep
    the download server-side and avoid an extra HTTP round-trip.
    Raises StorageError on failure.
    """
    client = _get_s3_client()
    try:
        response = client.get_object(Bucket=settings.r2_bucket_name, Key=r2_key)
        return response["Body"].read()
    except Exception as exc:
        raise StorageError(f"Failed to download {r2_key}: {exc}") from exc


# ── Query ─────────────────────────────────────────────────────────────────────

async def _fetch_requests(
    db: AsyncSession,
    status_filter: LeaveStatus | None,
    date_from: date | None,
    date_to: date | None,
) -> list[LeaveRequest]:
    """Fetch leave requests with all relationships needed for export."""
    query = (
        select(LeaveRequest)
        .where(LeaveRequest.is_deleted == False)            # noqa: E712
        .options(
            selectinload(LeaveRequest.student).selectinload(Student.user),
            selectinload(LeaveRequest.assigned_proctor).selectinload(Faculty.user),
        )
        .order_by(LeaveRequest.created_at.asc())
    )

    if status_filter:
        query = query.where(LeaveRequest.status == status_filter)
    if date_from:
        query = query.where(LeaveRequest.start_date >= date_from)
    if date_to:
        query = query.where(LeaveRequest.end_date <= date_to)

    result = await db.execute(query)
    return list(result.scalars().all())


# ── Manifest CSV ──────────────────────────────────────────────────────────────

MANIFEST_COLUMNS = [
    "request_id",
    "student_name",
    "registration_no",
    "student_email",
    "proctor_name",
    "proctor_email",
    "start_date",
    "end_date",
    "duration_days",
    "status",
    "proctor_remarks",
    "hod_remarks",
    "submitted_at",
    "pdf_filename",
]


def _build_manifest_csv(rows: list[dict]) -> bytes:
    """Serialise manifest rows to UTF-8 CSV bytes with BOM for Excel compatibility."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=MANIFEST_COLUMNS, lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows)
    # Prepend UTF-8 BOM so Excel opens the file correctly without import wizard
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


# ── Main export function ──────────────────────────────────────────────────────

async def export_leave_requests_zip(
    db: AsyncSession,
    status_filter: LeaveStatus | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> tuple[io.BytesIO, str]:
    """
    Build and return a (zip_buffer, filename) tuple.

    The zip_buffer is a rewound BytesIO ready to be streamed by FastAPI's
    StreamingResponse. The filename includes a timestamp so repeated exports
    don't overwrite each other on the client's filesystem.

    Steps:
      1. Query DB for matching leave requests
      2. Build the manifest CSV in memory
      3. For each request with a pdf_r2_key, download from R2 and add to ZIP
      4. Add the manifest as the last file in the ZIP
      5. Rewind and return
    """
    requests = await _fetch_requests(db, status_filter, date_from, date_to)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    folder_name = f"medical_leave_export_{timestamp}"
    zip_filename = f"{folder_name}.zip"

    zip_buffer = io.BytesIO()
    manifest_rows: list[dict] = []
    pdf_errors: list[str] = []

    with zipfile.ZipFile(zip_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for req in requests:
            student      = req.student
            student_user = student.user
            proctor      = req.assigned_proctor
            proctor_user = proctor.user

            pdf_fname = "N/A"

            # Download and add PDF if available
            if req.pdf_r2_key:
                pdf_fname = _pdf_filename(
                    reg_no    = student.registration_no,
                    full_name = student_user.full_name,
                    start     = req.start_date,
                    end       = req.end_date,
                )
                try:
                    pdf_bytes = _download_pdf_bytes(req.pdf_r2_key)
                    zf.writestr(f"{folder_name}/pdfs/{pdf_fname}", pdf_bytes)
                except StorageError as exc:
                    # Log the error in a sidecar file rather than crashing the export
                    pdf_errors.append(f"{req.id}: {exc}")
                    pdf_fname = f"ERROR_{pdf_fname}"

            manifest_rows.append({
                "request_id":      str(req.id),
                "student_name":    student_user.full_name,
                "registration_no": student.registration_no,
                "student_email":   student_user.email,
                "proctor_name":    f"{proctor.honorific} {proctor_user.full_name}",
                "proctor_email":   proctor_user.email,
                "start_date":      req.start_date.isoformat(),
                "end_date":        req.end_date.isoformat(),
                "duration_days":   req.duration_days,
                "status":          req.status.value,
                "proctor_remarks": req.proctor_remarks or "",
                "hod_remarks":     req.hod_remarks or "",
                "submitted_at":    req.created_at.strftime("%Y-%m-%d %H:%M:%S UTC"),
                "pdf_filename":    pdf_fname,
            })

        # Add manifest CSV
        zf.writestr(f"{folder_name}/manifest.csv", _build_manifest_csv(manifest_rows))

        # Add error log if any PDFs failed to download
        if pdf_errors:
            error_content = "PDF download errors during export:\n\n" + "\n".join(pdf_errors)
            zf.writestr(f"{folder_name}/pdf_errors.txt", error_content.encode("utf-8"))

    zip_buffer.seek(0)
    return zip_buffer, zip_filename
