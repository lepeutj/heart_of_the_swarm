from enum import StrEnum


class NodeType(StrEnum):
    INPUT = "input"
    AGENT = "agent"
    LLM = "llm"
    TOOL = "tool"
    CONDITION = "condition"
    TRANSFORM = "transform"
    OUTPUT = "output"


class ConditionOperator(StrEnum):
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    EXISTS = "exists"
    NOT_EXISTS = "not_exists"
    CONTAINS = "contains"
    GREATER_THAN = "greater_than"
    LESS_THAN = "less_than"
