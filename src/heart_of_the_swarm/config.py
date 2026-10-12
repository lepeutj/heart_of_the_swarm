import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from heart_of_the_swarm.http_policy import HTTPPolicy
from heart_of_the_swarm.network_policy import NetworkPolicy


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", enable_decoding=False)

    openai_api_key: SecretStr | None = None
    openai_base_url: str | None = None
    openrouter_api_key: SecretStr | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_site_url: str | None = None
    openrouter_app_name: str = "Heart of the Swarm"

    auth_mode: Literal["development", "jwt"] = "development"
    development_auth_token: SecretStr | None = None
    development_auth_user_id: str | None = None
    jwt_verification_key: SecretStr | None = None
    jwt_algorithm: str = "RS256"
    jwt_issuer: str | None = None
    jwt_audience: str | None = None
    jwt_clock_skew_seconds: int = Field(default=0, ge=0, le=300)

    builder_provider: str = "openai"
    builder_model: str = "gpt-4.1-mini"
    default_agent_provider: str = "openai"
    default_agent_model: str = "gpt-4.1-mini"
    allowed_agent_models: list[str] = Field(default_factory=list)
    model_catalog_ttl_seconds: int = Field(default=900, ge=30, le=86_400)

    database_url: str = "sqlite+aiosqlite:///./data/heart_of_the_swarm.db"
    connector_databases: dict[str, str] = Field(default_factory=dict)
    mcp_servers: dict[str, str] = Field(default_factory=dict)
    mcp_bearer_credentials: dict[str, str] = Field(default_factory=dict)
    capability_policies: dict[str, list[str]] = Field(default_factory=dict)
    capability_approval_policies: dict[str, list[str]] = Field(default_factory=dict)
    runtime_id: str = "local-runtime"
    runtime_api_token: SecretStr | None = None
    runtime_max_retained_runs: int = Field(default=100, ge=1, le=10_000)
    credential_policies: dict[str, list[str]] = Field(default_factory=dict)
    local_secrets: dict[str, SecretStr] = Field(default_factory=dict)
    request_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    max_document_bytes: int = Field(default=1_000_000, ge=1_000, le=5_000_000)
    http_max_request_bytes: int = Field(default=2_000_000, ge=1_024, le=100_000_000)
    network_allow_public: bool = True
    network_allow_private: bool = False
    network_allowed_cidrs: list[str] = Field(default_factory=list)
    network_denied_cidrs: list[str] = Field(default_factory=list)
    network_allow_loopback: bool = False
    network_allow_link_local: bool = False
    network_allow_metadata: bool = False

    api_log_file: str = "logs/api.jsonl"
    worker_log_file: str = "logs/worker.jsonl"
    log_level: str = "INFO"
    log_max_bytes: int = Field(default=10_000_000, ge=100_000)
    log_backup_count: int = Field(default=5, ge=1, le=100)

    mlflow_enabled: bool = False
    mlflow_tracking_uri: str = "http://localhost:5000"
    mlflow_experiment: str = "heart-of-the-swarm"

    agent_artifact_dir: Path = Path("/app/artifact")
    workflow_artifact_dir: Path = Path("/app/artifact")
    skills_dir: Path = Path("skills")

    worker_poll_seconds: float = Field(default=1.0, gt=0, le=60)
    worker_lease_seconds: int = Field(default=60, ge=15, le=3_600)
    worker_heartbeat_seconds: int = Field(default=15, ge=5, le=300)
    worker_max_attempts: int = Field(default=3, ge=1, le=20)
    agent_timeout_seconds: float = Field(default=600, gt=0, le=3_600)
    workflow_timeout_seconds: float = Field(default=120, gt=0, le=3_600)
    workflow_recursion_limit: int = Field(default=100, ge=2, le=10_000)
    workflow_max_state_bytes: int = Field(default=5_000_000, ge=1_024, le=100_000_000)
    workflow_max_model_calls: int = Field(default=100, ge=1, le=10_000)
    workflow_max_tool_calls: int = Field(default=100, ge=1, le=10_000)

    @field_validator("allowed_agent_models", mode="before")
    @classmethod
    def parse_models(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("network_allowed_cidrs", "network_denied_cidrs", mode="before")
    @classmethod
    def parse_cidrs(cls, value: object) -> object:
        if isinstance(value, str):
            if value.lstrip().startswith("["):
                return json.loads(value)
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator(
        "connector_databases",
        "mcp_servers",
        "mcp_bearer_credentials",
        "capability_policies",
        "capability_approval_policies",
        "credential_policies",
        "local_secrets",
        mode="before",
    )
    @classmethod
    def parse_json_mapping(cls, value: object) -> object:
        if isinstance(value, str):
            return json.loads(value)
        return value

    @field_validator(
        "openai_api_key",
        "openrouter_api_key",
        "development_auth_token",
        "jwt_verification_key",
        "runtime_api_token",
        mode="before",
    )
    @classmethod
    def empty_secrets_are_unset(cls, value: object) -> object:
        return None if value == "" else value

    @model_validator(mode="after")
    def validate_worker_timing(self) -> "Settings":
        if self.worker_heartbeat_seconds * 2 >= self.worker_lease_seconds:
            raise ValueError("worker heartbeat must be less than half of the worker lease")
        unknown_mcp_credentials = set(self.mcp_bearer_credentials) - set(self.mcp_servers)
        if unknown_mcp_credentials:
            raise ValueError("MCP bearer credentials must reference configured MCP servers")
        return self

    def outbound_network_policy(self) -> NetworkPolicy:
        """Build the runtime-only outbound policy without placing it in product specifications."""
        return NetworkPolicy(
            allow_public=self.network_allow_public,
            allow_private=self.network_allow_private,
            allowed_cidrs=tuple(self.network_allowed_cidrs),
            denied_cidrs=tuple(self.network_denied_cidrs),
            allow_loopback=self.network_allow_loopback,
            allow_link_local=self.network_allow_link_local,
            allow_metadata=self.network_allow_metadata,
        )

    def http_policy(self) -> HTTPPolicy:
        """Build the inbound HTTP policy shared by control-plane and standalone APIs."""
        return HTTPPolicy(max_request_bytes=self.http_max_request_bytes)


@lru_cache
def get_settings() -> Settings:
    return Settings()
