from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    model_id: str = Field(min_length=1, max_length=200)
    temperature: float = Field(default=0, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1, le=200_000)


class AgentDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    goal: str = Field(min_length=1, max_length=500)
    tools: list[str] = Field(default_factory=list, max_length=20)
    skills: list[str] = Field(default_factory=list, max_length=20)
    instructions: str = Field(min_length=1, max_length=4_000)

    @field_validator("tools", "skills")
    @classmethod
    def names_are_unique(cls, names: list[str]) -> list[str]:
        if len(names) != len(set(names)):
            raise ValueError("names must not contain duplicates")
        return names

    @field_validator("skills")
    @classmethod
    def skill_names_are_safe(cls, skills: list[str]) -> list[str]:
        for skill in skills:
            if not skill or not skill.replace("_", "").replace("-", "").isalnum():
                raise ValueError(
                    "skill names may contain only letters, numbers, hyphens, and underscores"
                )
        return skills


class AgentSpec(AgentDraft):
    model: ModelConfig


class DesignRequest(BaseModel):
    task: str = Field(min_length=1, max_length=4_000)
    provider: str | None = None
    model_id: str | None = None


class DesignResponse(BaseModel):
    design_id: str
    trace_id: str
    spec: AgentSpec


class ValidationResponse(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)


class AgentSummary(BaseModel):
    id: str
    version_id: str
    name: str
    goal: str
    version: int
    created_at: datetime


class AgentDetail(AgentSummary):
    spec: AgentSpec
    prompt_version: str
    system_prompt: str


class AgentRunRequest(BaseModel):
    input: str = Field(min_length=1, max_length=10_000)


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    CANCEL_REQUESTED = "cancel_requested"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    INTERRUPTED = "interrupted"


class AgentRunAccepted(BaseModel):
    run_id: str
    trace_id: str
    agent_id: str
    agent_version: int
    status: RunStatus


class AgentRunDetail(AgentRunAccepted):
    input: str
    output: str | None = None
    error: str | None = None
    attempt: int
    queued_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class RunEvent(BaseModel):
    id: str
    run_id: str
    event_type: str
    data: dict[str, object]
    created_at: datetime


class TrajectoryStep(BaseModel):
    id: str
    run_id: str
    attempt: int
    sequence: int
    event_type: str
    component: str | None = None
    langchain_run_id: str | None = None
    parent_run_id: str | None = None
    payload: dict[str, object]
    created_at: datetime


class ModelDescriptor(BaseModel):
    provider: str
    model_id: str
    name: str
    context_length: int | None = None
    prompt_price: str | None = None
    completion_price: str | None = None
    supports_tools: bool = False
    supports_structured_output: bool = False


class UsageSummary(BaseModel):
    provider: str
    model_id: str
    stage: str
    calls: int
    failures: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost: float
    average_latency_ms: float
