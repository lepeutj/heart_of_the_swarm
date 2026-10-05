from pathlib import Path

import pytest

from heart_of_the_swarm.factory import render_system_prompt
from heart_of_the_swarm.skills import SkillRegistry
from heart_of_the_swarm.spec import AgentSpec


def agent_spec(skills: list[str]) -> AgentSpec:
    return AgentSpec(
        name="ResearchAgent",
        goal="Research one topic",
        instructions="Answer clearly.",
        model={"provider": "test", "model_id": "test-model"},
        tools=[],
        skills=skills,
    )


def test_markdown_skills_are_injected_in_declared_order(tmp_path: Path) -> None:
    (tmp_path / "research.md").write_text("Verify multiple sources.", encoding="utf-8")
    (tmp_path / "summarize.md").write_text("Return a concise summary.", encoding="utf-8")

    prompt = render_system_prompt(agent_spec(["research", "summarize"]), SkillRegistry(tmp_path))

    assert prompt.index("Skill: research") < prompt.index("Skill: summarize")
    assert "Verify multiple sources." in prompt
    assert "Return a concise summary." in prompt


def test_unknown_skill_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown skills: missing"):
        render_system_prompt(agent_spec(["missing"]), SkillRegistry(tmp_path))
