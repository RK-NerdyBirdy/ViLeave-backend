"""
app/config.py
─────────────
Central configuration via Pydantic Settings.
All values are read from environment variables (or a .env file).
"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── Database ──────────────────────────────────────────────────────────────
    database_url: str               # asyncpg URL for runtime
    sync_database_url: str          # psycopg2 URL for Alembic migrations only

    # ── JWT ───────────────────────────────────────────────────────────────────
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480

    # ── Google OAuth ──────────────────────────────────────────────────────────
    google_client_id: str
    google_client_secret: str
    oauth_success_redirect_url: str = "http://localhost:3000/auth/callback"

    @property
    def google_redirect_uri(self) -> str:
        return f"{self.app_base_url.rstrip('/')}/auth/google/callback"

    # ── Cloudflare R2 ─────────────────────────────────────────────────────────
    r2_account_id: str
    r2_access_key_id: str
    r2_secret_access_key: str
    r2_bucket_name: str
    r2_endpoint: str
    r2_public_url: str              # https://<account-id>.r2.cloudflarestorage.com

    # ── Gmail SMTP ────────────────────────────────────────────────────────────
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_username: str
    smtp_password: str
    email_from_name: str = "Medical Leave System"

    # ── App ───────────────────────────────────────────────────────────────────
    app_env: str = "development"
    app_base_url: str = "http://localhost:8000"
    pdf_max_size_mb: int = 10

    @property
    def pdf_max_size_bytes(self) -> int:
        return self.pdf_max_size_mb * 1024 * 1024

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    """
    Cached singleton - the Settings object is created once and reused.
    Use FastAPI's Depends(get_settings) to inject into routes.
    """
    return Settings()
