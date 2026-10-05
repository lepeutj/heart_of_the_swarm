import sqlite3
from pathlib import Path

import httpx
import pytest

from heart_of_the_swarm.config import Settings
from heart_of_the_swarm.tools import builtin as builtin_tools
from heart_of_the_swarm.tools import create_default_registry
from heart_of_the_swarm.workflows import WorkflowGraphFactory, WorkflowSpec, WorkflowValidator


def connector_workflow() -> WorkflowSpec:
    return WorkflowSpec.model_validate(
        {
            "schema_version": "1",
            "id": "47d174a8-b35e-4563-bd86-3bc6b5b5947f",
            "name": "Database connector",
            "description": "Read rows through one deterministic capability.",
            "input_schema": {"type": "object"},
            "output_schema": None,
            "entrypoint": "input",
            "nodes": [
                {"id": "input", "type": "input", "name": "Input", "config": {}},
                {
                    "id": "query",
                    "type": "connector",
                    "name": "Query",
                    "config": {
                        "capability_id": "database_query",
                        "inputs": {
                            "connection": "analytics",
                            "statement": "SELECT name FROM people WHERE id >= :minimum",
                            "parameters": {"minimum": {"from_state": "$.minimum"}},
                        },
                        "outputs": {
                            "rows": {"to_state": "$.database.rows"},
                            "truncated": {"to_state": "$.database.truncated"},
                        },
                    },
                },
                {
                    "id": "count",
                    "type": "connector",
                    "name": "Count",
                    "config": {
                        "capability_id": "database_query",
                        "inputs": {
                            "connection": "analytics",
                            "statement": "SELECT COUNT(*) AS total FROM people",
                        },
                        "outputs": {
                            "rows": {"to_state": "$.database.count"},
                            "truncated": {"to_state": "$.database.count_truncated"},
                        },
                    },
                },
                {
                    "id": "output",
                    "type": "output",
                    "name": "Output",
                    "config": {
                        "outputs": {
                            "rows": {"from_state": "$.database.rows"},
                            "truncated": {"from_state": "$.database.truncated"},
                            "count": {"from_state": "$.database.count"},
                        }
                    },
                },
            ],
            "edges": [
                {"source": "input", "target": "query"},
                {"source": "query", "target": "count"},
                {"source": "count", "target": "output"},
            ],
        }
    )


async def test_database_connector_executes_real_query_and_maps_outputs(tmp_path: Path) -> None:
    database_path = tmp_path / "analytics.db"
    with sqlite3.connect(database_path) as database:
        database.execute("CREATE TABLE people (id INTEGER PRIMARY KEY, name TEXT)")
        database.executemany("INSERT INTO people (name) VALUES (?)", [("Alice",), ("Bob",)])

    settings = Settings(
        connector_databases={"analytics": f"sqlite+aiosqlite:///{database_path.as_posix()}"}
    )
    registry = create_default_registry(settings)
    validated = WorkflowValidator(registry.names, []).validate(connector_workflow())

    result = (
        await WorkflowGraphFactory(capabilities=registry).create(validated).ainvoke({"minimum": 1})
    )

    assert result.executed_nodes == ("input", "query", "count", "output")
    assert result.output == {
        "rows": [{"name": "Alice"}, {"name": "Bob"}],
        "truncated": False,
        "count": [{"total": 2}],
    }


async def test_http_json_connector_uses_real_json_decoder(monkeypatch) -> None:
    async def response(url: str) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok"}, request=httpx.Request("GET", url))

    monkeypatch.setattr(builtin_tools, "_fetch_public_url", response)

    result = await builtin_tools.http_get_json.ainvoke({"url": "https://example.com/data"})

    assert result == {"status": "ok"}


@pytest.mark.parametrize("content_type", ["application/atom+xml; charset=utf-8", "application/xml"])
async def test_document_reader_accepts_xml_feeds_as_text(monkeypatch, content_type: str) -> None:
    feed = "<feed><entry><title>Agent research</title></entry></feed>"

    async def response(url: str) -> httpx.Response:
        return httpx.Response(
            200,
            text=feed,
            headers={"Content-Type": content_type},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(builtin_tools, "_fetch_public_url", response)

    result = await builtin_tools.document_reader.ainvoke(
        {"url": "https://export.arxiv.org/api/query?search_query=cat:cs.AI"}
    )

    assert result == feed


@pytest.mark.parametrize(
    ("feed", "expected"),
    [
        (
            """<rss><channel><item><title>First story</title><link>https://example.com/1</link>
            <pubDate>Mon, 05 Oct 2026 08:00:00 GMT</pubDate>
            <description><![CDATA[<p>RSS summary</p>]]></description></item></channel></rss>""",
            {
                "title": "First story",
                "url": "https://example.com/1",
                "published_at": "Mon, 05 Oct 2026 08:00:00 GMT",
                "summary": "RSS summary",
            },
        ),
        (
            """<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Atom story</title>
            <link href="https://example.com/atom"/><updated>2026-10-05T08:00:00Z</updated>
            <summary>Atom summary</summary></entry></feed>""",
            {
                "title": "Atom story",
                "url": "https://example.com/atom",
                "published_at": "2026-10-05T08:00:00Z",
                "summary": "Atom summary",
            },
        ),
    ],
)
async def test_rss_reader_normalizes_real_feed_formats(monkeypatch, feed, expected) -> None:
    async def response(url: str) -> httpx.Response:
        return httpx.Response(
            200,
            content=feed.encode(),
            headers={"Content-Type": "application/xml"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(builtin_tools, "_fetch_public_url", response)

    result = await builtin_tools.rss_reader.ainvoke(
        {"url": "https://example.com/feed.xml", "max_items": 1}
    )

    assert result == {"feed_url": "https://example.com/feed.xml", "items": [expected]}


async def test_rss_reader_rejects_malformed_feed(monkeypatch) -> None:
    async def response(url: str) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"<rss><broken>",
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(builtin_tools, "_fetch_public_url", response)

    with pytest.raises(ValueError, match="valid RSS or Atom"):
        await builtin_tools.rss_reader.ainvoke({"url": "https://example.com/feed.xml"})


async def test_rss_reader_rejects_entity_declarations(monkeypatch) -> None:
    async def response(url: str) -> httpx.Response:
        return httpx.Response(
            200,
            content=b'<!DOCTYPE rss [<!ENTITY payload "unsafe">]><rss>&payload;</rss>',
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(builtin_tools, "_fetch_public_url", response)

    with pytest.raises(ValueError, match="DTD or entity"):
        await builtin_tools.rss_reader.ainvoke({"url": "https://example.com/feed.xml"})
