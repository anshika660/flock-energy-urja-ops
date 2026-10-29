import os
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    urja_portal_url: str = "https://urja-ops.flockenergy.tech"
    urja_username: str = "operator@urja.local"
    urja_password: str = "urja-ops-2026"

    port: int = 8000
    host: str = "0.0.0.0"
    verify_ssl: bool = False
    cache_ttl_seconds: int = 300
    request_timeout_seconds: float = 15.0
    max_retries: int = 3
    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()

