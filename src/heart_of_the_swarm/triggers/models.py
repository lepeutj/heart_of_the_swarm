from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TriggerType(StrEnum):
    MANUAL = "manual"
    WEBHOOK = "webhook"
    SCHEDULE = "schedule"


class TriggerTargetType(StrEnum):
    AGENT = "agent"
    WORKFLOW = "workflow"


class ManualTriggerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WebhookTriggerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScheduleTriggerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    every_seconds: int = Field(ge=60, le=31_536_000)


TriggerConfig = ManualTriggerConfig | WebhookTriggerConfig | ScheduleTriggerConfig

_CONFIG_TYPES: dict[TriggerType, type[TriggerConfig]] = {
    TriggerType.MANUAL: ManualTriggerConfig,
    TriggerType.WEBHOOK: WebhookTriggerConfig,
    TriggerType.SCHEDULE: ScheduleTriggerConfig,
}


class TriggerSpec(BaseModel):
    """Serializable trigger definition targeting one immutable product version."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    type: TriggerType
    target_type: TriggerTargetType
    target_version_id: UUID
    enabled: bool = True
    config: TriggerConfig

    @model_validator(mode="before")
    @classmethod
    def parse_typed_config(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        trigger_type = TriggerType(value.get("type"))
        parsed = dict(value)
        parsed["config"] = _CONFIG_TYPES[trigger_type].model_validate(value.get("config", {}))
        return parsed


class TriggerDetail(TriggerSpec):
    id: UUID
    next_fire_at: datetime | None = None
    last_fired_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
