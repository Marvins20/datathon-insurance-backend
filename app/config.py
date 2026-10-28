from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str = ""
    llm_model_main: str = "claude-opus-5-5"
    llm_model_fast: str = "claude-haiku-5-5"

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/postgres"
    supabase_url: str = ""
    supabase_jwt_secret: str = ""
    supabase_service_role_key: str = ""

    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    whatsapp_provider: Literal["simulator", "meta", "twilio"] = "simulator"
    meta_wa_token: str = ""
    meta_wa_phone_number_id: str = ""
    meta_wa_verify_token: str = ""
    meta_app_secret: str = ""
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_wa_from: str = ""

    kg_version: str = "latest"
    demo_mode: bool = False
    enrichment_timeout_s: float = 5
    enable_dev_routes: bool = False

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            return [o.strip() for o in value.split(",") if o.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
