from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class SkillDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    content: str = Field(min_length=1)


class SkillRegistry:
    """Resolve reusable Markdown instructions from one configured directory."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    @property
    def names(self) -> tuple[str, ...]:
        if not self.directory.exists():
            return ()
        return tuple(sorted(path.stem for path in self.directory.glob("*.md") if path.is_file()))

    def resolve(self, names: list[str]) -> list[tuple[str, str]]:
        available = set(self.names)
        unknown = [name for name in names if name not in available]
        if unknown:
            raise ValueError(f"unknown skills: {', '.join(unknown)}")
        return [
            (name, (self.directory / f"{name}.md").read_text(encoding="utf-8")) for name in names
        ]

    def save(self, skill: SkillDocument) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / f"{skill.name}.md"
        if target.exists():
            raise ValueError(f"skill already exists: {skill.name}")
        target.write_text(skill.content, encoding="utf-8")
