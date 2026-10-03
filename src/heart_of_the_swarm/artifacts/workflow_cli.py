import argparse
import asyncio
from pathlib import Path
from uuid import UUID

from heart_of_the_swarm.artifacts.service import WorkflowArtifactExporter
from heart_of_the_swarm.config import Settings
from heart_of_the_swarm.database import Database


async def export_workflow(version_id: UUID, output: Path) -> None:
    database = Database(Settings().database_url)
    try:
        artifact = await WorkflowArtifactExporter(database).export(version_id, output)
    finally:
        await database.close()
    if artifact is None:
        raise SystemExit(f"workflow version not found: {version_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export an immutable workflow version")
    parser.add_argument("version_id", type=UUID)
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args()
    asyncio.run(export_workflow(arguments.version_id, arguments.output))


if __name__ == "__main__":
    main()
