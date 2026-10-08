from heart_of_the_swarm.credentials import CredentialRef
from heart_of_the_swarm.database import Database
from heart_of_the_swarm.repositories.mcp_servers import MCPServerRepository
from heart_of_the_swarm.tools import MCPConnection, MCPServerCreate, MCPServerDetail


class MCPServerService:
    """Manage persisted MCP source definitions without owning runtime capabilities."""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def seed(
        self,
        sources: dict[str, str],
        bearer_credentials: dict[str, str] | None = None,
    ) -> None:
        validated = {}
        for name, url in sources.items():
            credential_id = (bearer_credentials or {}).get(name)
            source = MCPServerCreate(
                name=name,
                url=url,
                bearer_credential_ref=(
                    CredentialRef(id=credential_id) if credential_id is not None else None
                ),
            )
            validated[source.name] = source
        async with self.database.session() as session:
            await MCPServerRepository(session).seed(validated)

    async def list(self) -> list[MCPServerDetail]:
        async with self.database.session() as session:
            return await MCPServerRepository(session).list()

    async def get(self, server_id: str) -> MCPServerDetail | None:
        async with self.database.session() as session:
            return await MCPServerRepository(session).get(server_id)

    async def create(self, source: MCPServerCreate) -> MCPServerDetail:
        async with self.database.session() as session:
            return await MCPServerRepository(session).create(source)

    async def enabled_sources(self) -> dict[str, MCPConnection]:
        return {
            source.name: MCPConnection(
                url=source.url,
                bearer_credential_ref=source.bearer_credential_ref,
            )
            for source in await self.list()
            if source.enabled
        }
