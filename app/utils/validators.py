"""
app/utils/validators.py
────────────────────────
Reusable format validators used in schemas and the CSV ingest service.
"""
import re
from datetime import date


def is_valid_registration_no(value: str) -> bool:
    """24XXX0001 — 2-digit year, 3 uppercase letters, 4 digits."""
    return bool(re.fullmatch(r"\d{2}[A-Z]{3}\d{4}", value))


def is_valid_faculty_id(value: str) -> bool:
    """Exactly 5 numeric digits."""
    return bool(re.fullmatch(r"\d{5}", value))


def compute_duration(start: date, end: date) -> int:
    """Inclusive day count: start=Mon, end=Wed → 3 days."""
    return (end - start).days + 1


def dates_overlap(
    a_start: date, a_end: date,
    b_start: date, b_end: date,
) -> bool:
    """True if two date ranges share at least one calendar day."""
    return a_start <= b_end and b_start <= a_end
