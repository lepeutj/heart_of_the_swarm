from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field


class CapabilityDescriptor(BaseModel):
    """Serializable catalogue data for one registered runtime capability."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    source: Literal["builtin", "mcp"]
    origin: str | None = None
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None
    annotations: dict[str, Any]
    schema_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class CapabilityContract(BaseModel):
    """Immutable capability identity captured by a published workflow version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    capability_id: str
    source: Literal["builtin", "mcp"]
    origin: str | None = None
    schema_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class MCPServerCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z][a-z0-9_-]*$", min_length=1, max_length=50)
    url: AnyHttpUrl = Field(max_length=2048)
    enabled: bool = True


class MCPServerDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str
    url: str
    enabled: bool
    created_at: datetime
    updated_at: datetime


class MCPServerStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    state: Literal["ready", "error", "not_loaded"]
    tools: tuple[str, ...] = ()
    error: str | None = None


class MCPServerView(MCPServerDetail):
    status: MCPServerStatus


class MCPServerTestResult(BaseModel):
    name: str
    reachable: bool
    tools: tuple[str, ...] = ()
    error: str | None = None


class ToolCatalogueResponse(BaseModel):
    tools: tuple[str, ...]
    capabilities: tuple[CapabilityDescriptor, ...]
    mcp_servers: tuple[MCPServerStatus, ...]
