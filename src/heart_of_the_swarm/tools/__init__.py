from heart_of_the_swarm.tools.builtin import calculator, document_reader, web_search
from heart_of_the_swarm.tools.registry import ToolRegistry


def create_default_registry() -> ToolRegistry:
    return ToolRegistry([web_search, calculator, document_reader])


__all__ = ["ToolRegistry", "create_default_registry"]
