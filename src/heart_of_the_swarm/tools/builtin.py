import ast
import ipaddress
import operator
import socket
import time
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from langchain_core.tools import tool
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from heart_of_the_swarm.config import Settings, get_settings
from heart_of_the_swarm.observability import audit_event, audit_exception

_BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPERATORS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _evaluate(node: ast.AST) -> int | float:
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
        left, right = _evaluate(node.left), _evaluate(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 100:
            raise ValueError("exponent is too large")
        result = _BINARY_OPERATORS[type(node.op)](left, right)
        if abs(result) > 1e100:
            raise ValueError("result is too large")
        return result
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPERATORS:
        return _UNARY_OPERATORS[type(node.op)](_evaluate(node.operand))
    raise ValueError("only numbers, parentheses, and basic arithmetic are allowed")


@tool
def calculator(expression: str) -> str:
    """Evaluate a basic arithmetic expression without executing code."""
    if len(expression) > 200:
        raise ValueError("expression is too long")
    started = time.perf_counter()
    try:
        parsed = ast.parse(expression, mode="eval")
        result = str(_evaluate(parsed.body))
    except Exception:
        audit_exception("calculator.failed", expression_characters=len(expression))
        raise
    audit_event(
        "calculator.completed",
        expression_characters=len(expression),
        duration_ms=round((time.perf_counter() - started) * 1000, 2),
    )
    return result


async def _ensure_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL must use http or https")
    if parsed.username or parsed.password:
        raise ValueError("URLs containing credentials are not allowed")

    try:
        addresses = (
            await __import__("asyncio")
            .get_running_loop()
            .run_in_executor(None, lambda: socket.getaddrinfo(parsed.hostname, parsed.port or 443))
        )
    except socket.gaierror as exc:
        raise ValueError("URL hostname could not be resolved") from exc

    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise ValueError("private, loopback, and reserved addresses are not allowed")


async def _fetch_public_url(url: str) -> httpx.Response:
    settings = get_settings()
    headers = {"User-Agent": "heart-of-the-swarm/0.1"}
    async with httpx.AsyncClient(
        timeout=settings.request_timeout_seconds, headers=headers
    ) as client:
        current_url = url
        for _ in range(4):
            parsed = urlparse(current_url)
            started = time.perf_counter()
            audit_event(
                "http.fetch.started",
                scheme=parsed.scheme,
                host=parsed.hostname,
                port=parsed.port,
            )
            await _ensure_public_url(current_url)
            try:
                response = await client.get(current_url, follow_redirects=False)
            except Exception:
                audit_exception(
                    "http.fetch.failed",
                    host=parsed.hostname,
                    duration_ms=round((time.perf_counter() - started) * 1000, 2),
                )
                raise
            audit_event(
                "http.fetch.completed",
                host=parsed.hostname,
                status_code=response.status_code,
                response_bytes=len(response.content),
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise ValueError("redirect had no destination")
                current_url = urljoin(current_url, location)
                continue
            response.raise_for_status()
            if len(response.content) > settings.max_document_bytes:
                raise ValueError("document is too large")
            return response
    raise ValueError("too many redirects")


@tool
async def web_search(query: str) -> str:
    """Search the public web and return up to five result titles, URLs, and snippets."""
    if not query.strip() or len(query) > 500:
        raise ValueError("query must contain between 1 and 500 characters")
    search_url = str(httpx.URL("https://html.duckduckgo.com/html/", params={"q": query}))
    response = await _fetch_public_url(search_url)
    soup = BeautifulSoup(response.text, "html.parser")
    results: list[str] = []
    for item in soup.select(".result")[:5]:
        link = item.select_one(".result__a")
        snippet = item.select_one(".result__snippet")
        if link and link.get("href"):
            results.append(
                f"{link.get_text(' ', strip=True)}\n{link['href']}\n"
                f"{snippet.get_text(' ', strip=True) if snippet else ''}"
            )
    audit_event("web_search.completed", query_characters=len(query), result_count=len(results))
    return "\n\n".join(results) or "No search results found."


@tool
async def document_reader(url: str) -> str:
    """Read text from a public HTML, plain-text, or XML URL."""
    response = await _fetch_public_url(url)
    content_type = response.headers.get("content-type", "").lower()
    media_type = content_type.partition(";")[0].strip()
    if media_type == "text/html":
        soup = BeautifulSoup(response.text, "html.parser")
        for element in soup(["script", "style", "noscript"]):
            element.decompose()
        text = soup.get_text(" ", strip=True)
    elif (
        media_type.startswith("text/")
        or media_type == "application/xml"
        or media_type.endswith("+xml")
        or not media_type
    ):
        text = response.text
    else:
        raise ValueError(f"unsupported content type: {content_type}")
    output = text[:20_000]
    audit_event(
        "document_reader.completed",
        host=urlparse(url).hostname,
        content_type=content_type,
        output_characters=len(output),
    )
    return output


def _xml_text(element: ET.Element, names: tuple[str, ...]) -> str:
    for child in element.iter():
        if child.tag.rsplit("}", 1)[-1] in names and child.text:
            return child.text.strip()
    return ""


def _feed_link(element: ET.Element) -> str:
    for child in element.iter():
        if child.tag.rsplit("}", 1)[-1] != "link":
            continue
        href = child.get("href")
        if href and child.get("rel", "alternate") == "alternate":
            return href.strip()
        if child.text:
            return child.text.strip()
    return ""


@tool
async def rss_reader(url: str, max_items: int = 5) -> dict:
    """Read a public RSS or Atom feed and return a limited list of normalized entries."""
    if not 1 <= max_items <= 20:
        raise ValueError("max_items must be between 1 and 20")
    response = await _fetch_public_url(url)
    xml_content = response.content
    normalized_xml = xml_content.upper()
    if b"<!DOCTYPE" in normalized_xml or b"<!ENTITY" in normalized_xml:
        raise ValueError("RSS and Atom feeds cannot contain DTD or entity declarations")
    try:
        # The bounded response is parsed only after DTD and entity declarations are rejected.
        root = ET.fromstring(xml_content)  # noqa: S314
    except ET.ParseError as exc:
        raise ValueError("response is not a valid RSS or Atom feed") from exc

    entries = [
        element for element in root.iter() if element.tag.rsplit("}", 1)[-1] in {"item", "entry"}
    ][:max_items]
    items = []
    for entry in entries:
        summary = _xml_text(entry, ("description", "summary", "content"))
        items.append(
            {
                "title": _xml_text(entry, ("title",)),
                "url": _feed_link(entry),
                "published_at": _xml_text(entry, ("pubDate", "published", "updated", "date")),
                "summary": BeautifulSoup(summary, "html.parser").get_text(" ", strip=True)[:1_000],
            }
        )
    audit_event(
        "rss_reader.completed",
        host=urlparse(url).hostname,
        item_count=len(items),
    )
    return {"feed_url": str(response.url), "items": items}


@tool
async def http_get_json(url: str) -> dict | list:
    """Fetch one public HTTP endpoint and return its JSON response."""
    response = await _fetch_public_url(url)
    return response.json()


def create_database_query(settings: Settings):
    @tool
    async def database_query(
        connection: str,
        statement: str,
        parameters: dict | None = None,
        max_rows: int = 1_000,
    ) -> dict:
        """Run one read-only SQL query against a configured database connection."""
        if connection not in settings.connector_databases:
            raise ValueError(f"unknown database connection: {connection}")
        normalized = statement.strip().rstrip(";")
        if not normalized.lower().startswith(("select ", "with ")) or ";" in normalized:
            raise ValueError("database_query accepts one SELECT or WITH statement")
        if not 1 <= max_rows <= 10_000:
            raise ValueError("max_rows must be between 1 and 10000")
        engine = create_async_engine(settings.connector_databases[connection])
        try:
            async with engine.connect() as database:
                result = await database.execute(text(normalized), parameters or {})
                rows = [dict(row) for row in result.mappings().fetchmany(max_rows + 1)]
        finally:
            await engine.dispose()
        return {"rows": rows[:max_rows], "truncated": len(rows) > max_rows}

    return database_query
