from __future__ import annotations

import json
from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from jsonschema.validators import validator_for
from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool

from heart_of_the_swarm.tools.models import CapabilityContract, CapabilityDescriptor

if TYPE_CHECKING:
    from heart_of_the_swarm.workflows.execution.execution_budget import ExecutionBudget

CapabilitySource = Literal["builtin", "mcp"]


def public_tool_schema(tool: BaseTool) -> dict[str, Any]:
    """Return only arguments supplied by a model or deterministic caller."""
    schema = tool.tool_call_schema
    if isinstance(schema, dict):
        return deepcopy(schema)
    return deepcopy(schema.model_json_schema())


def validate_public_tool_arguments(tool: BaseTool, arguments: dict[str, Any]) -> dict[str, Any]:
    """Validate caller-owned arguments without requiring LangChain-injected values."""
    schema = tool.tool_call_schema
    if isinstance(schema, dict):
        validator_for(schema)(schema).validate(arguments)
        return deepcopy(arguments)
    return schema.model_validate(arguments).model_dump(mode="json")


@dataclass(frozen=True)
class RegisteredCapability:
    """One executable LangChain tool and its catalogue provenance."""

    id: str
    tool: BaseTool
    source: CapabilitySource
    origin: str | None = None

    def __post_init__(self) -> None:
        if self.id != self.tool.name:
            raise ValueError("capability id must match the LangChain tool name")
        if self.source == "mcp" and not self.origin:
            raise ValueError("MCP capabilities require a server origin")

    @property
    def input_schema(self) -> dict[str, Any]:
        """Return the tool input schema without exposing mutable registry state."""
        return public_tool_schema(self.tool)

    @property
    def annotations(self) -> dict[str, Any]:
        """Return optional MCP annotations supplied by the remote server."""
        metadata = self.tool.metadata or {}
        mcp = metadata.get("mcp", {})
        tool_metadata = mcp.get("tool", {}) if isinstance(mcp, dict) else {}
        annotations = tool_metadata.get("annotations", {})
        return deepcopy(annotations) if isinstance(annotations, dict) else {}

    @property
    def output_schema(self) -> dict[str, Any] | None:
        return None

    @property
    def schema_fingerprint(self) -> str:
        payload = json.dumps(
            {"input_schema": self.input_schema, "output_schema": self.output_schema},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return sha256(payload).hexdigest()

    def descriptor(self) -> CapabilityDescriptor:
        return CapabilityDescriptor(
            id=self.id,
            source=self.source,
            origin=self.origin,
            description=self.tool.description,
            input_schema=self.input_schema,
            output_schema=self.output_schema,
            annotations=self.annotations,
            schema_fingerprint=self.schema_fingerprint,
        )

    def contract(self) -> CapabilityContract:
        return CapabilityContract(
            capability_id=self.id,
            source=self.source,
            origin=self.origin,
            schema_fingerprint=self.schema_fingerprint,
        )


class ToolRegistry:
    """A trusted registry shared by agents and deterministic connectors."""

    def __init__(self, tools: Iterable[BaseTool | RegisteredCapability] = ()) -> None:
        self._capabilities: dict[str, RegisteredCapability] = {}
        for item in tools:
            capability = (
                item
                if isinstance(item, RegisteredCapability)
                else RegisteredCapability(id=item.name, tool=item, source="builtin")
            )
            self.register(capability)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._capabilities)

    @property
    def capabilities(self) -> tuple[RegisteredCapability, ...]:
        return tuple(self._capabilities.values())

    def register(self, capability: RegisteredCapability) -> None:
        """Register one trusted capability without replacing an existing ID."""
        if capability.id in self._capabilities:
            raise ValueError(f"capability already registered: {capability.id}")
        self._capabilities[capability.id] = capability

    def replace_source(
        self,
        source: CapabilitySource,
        origin: str,
        capabilities: Iterable[RegisteredCapability],
    ) -> None:
        """Replace one source catalogue atomically after validating every ID."""
        replacements = tuple(capabilities)
        invalid = [
            capability.id
            for capability in replacements
            if capability.source != source or capability.origin != origin
        ]
        if invalid:
            raise ValueError("replacement capabilities must match their source and origin")

        candidate = {
            name: capability
            for name, capability in self._capabilities.items()
            if not (capability.source == source and capability.origin == origin)
        }
        for capability in replacements:
            if capability.id in candidate:
                raise ValueError(f"capability already registered: {capability.id}")
            candidate[capability.id] = capability
        self._capabilities = candidate

    def remove_source(self, source: CapabilitySource, origin: str) -> None:
        """Remove one source catalogue without changing unrelated capabilities."""
        self._capabilities = {
            name: capability
            for name, capability in self._capabilities.items()
            if not (capability.source == source and capability.origin == origin)
        }

    def resolve(self, names: list[str]) -> list[BaseTool]:
        unknown = sorted(set(names) - self._capabilities.keys())
        if unknown:
            raise ValueError(f"unknown tools: {', '.join(unknown)}")
        return [self._capabilities[name].tool for name in names]

    def resolve_one(self, name: str) -> BaseTool:
        """Resolve one allow-listed tool without exposing the registry mapping."""
        try:
            return self._capabilities[name].tool
        except KeyError as exc:
            raise ValueError(f"unknown tool: {name}") from exc

    def describe(self, name: str) -> CapabilityDescriptor:
        return self._resolve_capability(name).descriptor()

    def contract(self, name: str) -> CapabilityContract:
        return self._resolve_capability(name).contract()

    def verify_contracts(self, contracts: Iterable[CapabilityContract]) -> None:
        """Reject missing or schema-incompatible versioned capabilities."""
        for expected in contracts:
            current = self._resolve_capability(expected.capability_id).contract()
            if current != expected:
                raise ValueError(f"capability contract changed: {expected.capability_id}")

    def validate_arguments(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Return the exact JSON arguments accepted by one capability schema."""
        tool = self._resolve_capability(name).tool
        return validate_public_tool_arguments(tool, arguments)

    async def invoke_one(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        config: RunnableConfig | None = None,
        execution_budget: ExecutionBudget | None = None,
    ) -> Any:
        """Invoke one capability and normalize framework-specific result envelopes."""
        capability = self._resolve_capability(name)
        validated_arguments = self.validate_arguments(name, arguments)
        if execution_budget is not None:
            await execution_budget.consume_tool_call()
        if capability.source != "mcp":
            execution_tool = capability.tool.model_copy(
                update={"handle_tool_error": False, "handle_validation_error": False}
            )
            return await execution_tool.ainvoke(validated_arguments, config=config)

        result = await capability.tool.ainvoke(
            {
                "name": capability.tool.name,
                "args": validated_arguments,
                "id": str(uuid4()),
                "type": "tool_call",
            },
            config=config,
        )
        if not isinstance(result, ToolMessage):
            return result
        if result.status == "error":
            raise RuntimeError(_text_content(result.content) or f"Capability '{name}' failed")

        artifact = result.artifact
        if isinstance(artifact, dict) and artifact.get("structured_content") is not None:
            return deepcopy(artifact["structured_content"])
        return _normalize_content(result.content)

    def _resolve_capability(self, name: str) -> RegisteredCapability:
        try:
            return self._capabilities[name]
        except KeyError as exc:
            raise ValueError(f"unknown tool: {name}") from exc


def _normalize_content(content: Any) -> Any:
    if isinstance(content, str):
        return content
    text = _text_content(content)
    return text if text is not None else deepcopy(content)


def _text_content(content: Any) -> str | None:
    if not isinstance(content, list):
        return None
    blocks: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "text":
            return None
        text = block.get("text")
        if not isinstance(text, str):
            return None
        blocks.append(text)
    return "\n".join(blocks)
