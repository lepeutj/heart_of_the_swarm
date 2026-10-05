import argparse
import asyncio
from pathlib import Path
from uuid import UUID

from heart_of_the_swarm.artifacts.service import AgentArtifactExporter
from heart_of_the_swarm.config import get_settings
from heart_of_the_swarm.database import Database


async def export_agent(version_id: UUID, output: Path) -> None:
    database = Database(get_settings().database_url)
    try:
        await database.initialize()
        artifact = await AgentArtifactExporter(database).export(version_id, output)
    finally:
        await database.close()
    if artifact is None:
        raise SystemExit(f"agent version '{version_id}' was not found")
    print(output.resolve())


def main() -> None:
    parser = argparse.ArgumentParser(description="Export an immutable agent version.")
    parser.add_argument("agent_version_id", type=UUID)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    asyncio.run(export_agent(args.agent_version_id, args.output))


if __name__ == "__main__":
    main()
