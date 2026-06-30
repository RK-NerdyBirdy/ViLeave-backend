"""
app/models/__init__.py
──────────────────────
Imports all models so Alembic's env.py can discover them via Base.metadata.
"""
from app.models.user import User, Student, Faculty          # noqa: F401
from app.models.leave_request import LeaveRequest           # noqa: F401
from app.models.system_config import SystemConfig           # noqa: F401
