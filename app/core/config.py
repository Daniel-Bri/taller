from typing import Optional
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # SMTP — configurar en .env para envío de correos
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    SMTP_FROM: Optional[str] = None

    @property
    def async_database_url(self) -> str:
        # Railway provee postgresql:// o postgres://, SQLAlchemy async necesita postgresql+asyncpg://
        url = self.DATABASE_URL
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        # asyncpg NO entiende sslmode en la URL — se maneja via connect_args en session.py
        if "sslmode=" in url:
            from urllib.parse import urlparse, urlencode, parse_qs, urlunparse
            parsed = urlparse(url)
            params = {k: v[0] for k, v in parse_qs(parsed.query).items() if k != "sslmode"}
            url = urlunparse(parsed._replace(query=urlencode(params)))
        return url

    @property
    def db_ssl(self) -> bool:
        """True si la DB remota requiere SSL (Railway, Heroku, Supabase, etc.)."""
        return "sslmode=require" in self.DATABASE_URL or "sslmode=verify-full" in self.DATABASE_URL

    class Config:
        env_file = ".env"


settings = Settings()
