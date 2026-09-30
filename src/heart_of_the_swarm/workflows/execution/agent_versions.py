from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from heart_of_the_swarm.spec import AgentSpec


@dataclass(frozen=True)
class ResolvedAgentVersion:
    """Immutable agent data required for one workflow invocation."""

    spec: AgentSpec
    system_prompt: str


class AgentVersionResolver(Protocol):
    """Resolve a saved agent version without coupling workflows to persistence."""

    async def resolve(self, agent_version_id: UUID) -> ResolvedAgentVersion | None: ...
