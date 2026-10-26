from decimal import Decimal
from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _environment_alias(name: str) -> AliasChoices:
    return AliasChoices(f"UVM_{name}", f"SBE_{name}")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="UVM_",
        env_file=".env",
        env_file_encoding="utf-8",
        populate_by_name=True,
        extra="ignore",
    )

    environment: Literal["development", "test", "production"] = Field(
        default="development", validation_alias=_environment_alias("ENVIRONMENT")
    )
    log_level: str = Field(
        default="INFO",
        pattern=r"^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$",
        validation_alias=_environment_alias("LOG_LEVEL"),
    )
    api_prefix: str = Field(
        default="/api/v1", pattern=r"^/", validation_alias=_environment_alias("API_PREFIX")
    )
    database_url: str | None = Field(
        default=None, validation_alias=_environment_alias("DATABASE_URL")
    )
    chroma_persist_directory: str = Field(
        default="data/chroma",
        validation_alias=_environment_alias("CHROMA_PERSIST_DIRECTORY"),
    )
    chroma_collection: str = Field(
        default="universal_video_knowledge",
        validation_alias=_environment_alias("CHROMA_COLLECTION"),
    )
    source_snapshot_directory: str = Field(
        default="data/source-snapshots",
        validation_alias=_environment_alias("SOURCE_SNAPSHOT_DIRECTORY"),
    )
    tavily_api_key: SecretStr | None = Field(
        default=None, validation_alias=_environment_alias("TAVILY_API_KEY")
    )
    tavily_api_base_url: str = Field(
        default="https://api.tavily.com",
        validation_alias=_environment_alias("TAVILY_API_BASE_URL"),
    )
    tavily_request_timeout_seconds: int = Field(
        default=30,
        ge=1,
        le=300,
        validation_alias=_environment_alias("TAVILY_REQUEST_TIMEOUT_SECONDS"),
    )
    tavily_max_results: int = Field(
        default=6,
        ge=1,
        le=20,
        validation_alias=_environment_alias("TAVILY_MAX_RESULTS"),
    )
    ollama_base_url: str = Field(
        default="http://localhost:11434",
        validation_alias=_environment_alias("OLLAMA_BASE_URL"),
    )
    ollama_chat_model: str = Field(
        default="qwen2.5:7b",
        min_length=1,
        validation_alias=_environment_alias("OLLAMA_CHAT_MODEL"),
    )
    ollama_embedding_model: str = Field(
        default="nomic-embed-text",
        min_length=1,
        validation_alias=_environment_alias("OLLAMA_EMBEDDING_MODEL"),
    )
    ollama_request_timeout_seconds: int = Field(
        default=180,
        ge=1,
        le=600,
        validation_alias=_environment_alias("OLLAMA_REQUEST_TIMEOUT_SECONDS"),
    )
    oidc_issuer_url: str = Field(
        default="http://localhost:8080/realms/universal-video-maker",
        validation_alias=_environment_alias("OIDC_ISSUER_URL"),
    )
    oidc_jwks_url: str = Field(
        default=(
            "http://localhost:8080/realms/universal-video-maker/protocol/openid-connect/certs"
        ),
        validation_alias=_environment_alias("OIDC_JWKS_URL"),
    )
    oidc_audience: str = Field(
        default="universal-video-maker-api",
        min_length=1,
        validation_alias=_environment_alias("OIDC_AUDIENCE"),
    )
    minio_endpoint_url: str = Field(
        default="http://localhost:9000",
        validation_alias=_environment_alias("MINIO_ENDPOINT_URL"),
    )
    minio_access_key: SecretStr | None = Field(
        default=None, validation_alias=_environment_alias("MINIO_ACCESS_KEY")
    )
    minio_secret_key: SecretStr | None = Field(
        default=None, validation_alias=_environment_alias("MINIO_SECRET_KEY")
    )
    minio_bucket: str = Field(
        default="universal-video-assets",
        min_length=3,
        max_length=63,
        validation_alias=_environment_alias("MINIO_BUCKET"),
    )
    minio_region: str = Field(
        default="us-east-1",
        min_length=1,
        validation_alias=_environment_alias("MINIO_REGION"),
    )
    runpod_api_key: SecretStr | None = Field(
        default=None, validation_alias=_environment_alias("RUNPOD_API_KEY")
    )
    runpod_serverless_endpoint_id: str | None = Field(
        default=None, validation_alias=_environment_alias("RUNPOD_SERVERLESS_ENDPOINT_ID")
    )
    runpod_api_base_url: str = Field(
        default="https://api.runpod.io/v2",
        validation_alias=_environment_alias("RUNPOD_API_BASE_URL"),
    )
    runpod_request_timeout_seconds: int = Field(
        default=30,
        ge=1,
        le=300,
        validation_alias=_environment_alias("RUNPOD_REQUEST_TIMEOUT_SECONDS"),
    )
    runpod_max_job_cost_usd: Decimal = Field(
        default=Decimal("25.00"),
        gt=0,
        validation_alias=_environment_alias("RUNPOD_MAX_JOB_COST_USD"),
    )
    runpod_monthly_budget_usd: Decimal = Field(
        default=Decimal("250.00"),
        gt=0,
        validation_alias=_environment_alias("RUNPOD_MONTHLY_BUDGET_USD"),
    )

    @field_validator("tavily_api_base_url")
    @classmethod
    def validate_tavily_api_base_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme.lower() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("Tavily API base URL must be an HTTPS origin.")
        return value.rstrip("/")

    @field_validator("ollama_base_url")
    @classmethod
    def validate_ollama_base_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        hostname = (parsed.hostname or "").lower()
        is_local_http = parsed.scheme.lower() == "http" and hostname in {
            "localhost",
            "127.0.0.1",
            "::1",
        }
        if (
            (parsed.scheme.lower() != "https" and not is_local_http)
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("Ollama base URL must be HTTPS, or HTTP on a loopback address.")
        return value.rstrip("/")

    @field_validator("oidc_issuer_url", "oidc_jwks_url", "minio_endpoint_url")
    @classmethod
    def validate_service_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        hostname = (parsed.hostname or "").lower()
        is_local_http = parsed.scheme.lower() == "http" and hostname in {
            "localhost",
            "127.0.0.1",
            "::1",
        }
        if (
            (parsed.scheme.lower() != "https" and not is_local_http)
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Service URLs must use HTTPS, or HTTP on a loopback address.")
        return value.rstrip("/")

    @property
    def is_development(self) -> bool:
        return self.environment == "development"


@lru_cache
def get_settings() -> Settings:
    return Settings()
