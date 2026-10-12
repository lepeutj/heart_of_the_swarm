"""Expose bounded sales analytics tools over MCP using a read-only PostgreSQL role."""

import os
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

import asyncpg
from fastmcp import FastMCP

DATABASE_URL = os.environ["SALES_DATABASE_URL"]

mcp = FastMCP(
    "Heart sales analytics",
    instructions=(
        "Use these read-only tools to inspect the demonstration sales database. "
        "The server exposes bounded business queries and never accepts arbitrary SQL."
    ),
)
app = mcp.http_app(path="/mcp")


def _json_value(value: Any) -> Any:
    """Convert database-native scalar values into stable MCP JSON values."""
    if isinstance(value, Decimal):
        return float(value)
    return value


def _rows(records: Sequence[asyncpg.Record]) -> list[dict[str, Any]]:
    return [{key: _json_value(value) for key, value in record.items()} for record in records]


async def _fetch(query: str, *arguments: object) -> list[asyncpg.Record]:
    """Execute one fixed query in an explicit read-only transaction."""
    connection = await asyncpg.connect(DATABASE_URL, command_timeout=5)
    try:
        async with connection.transaction(readonly=True):
            return list(await connection.fetch(query, *arguments))
    finally:
        await connection.close()


@mcp.tool
async def database_access() -> dict[str, Any]:
    """Show the database identity and verify that this MCP connection is read-only."""
    records = await _fetch(
        """
        SELECT
            current_user AS database_user,
            current_setting('transaction_read_only')::boolean AS transaction_read_only,
            has_table_privilege(current_user, 'orders', 'SELECT') AS may_select_orders,
            has_table_privilege(current_user, 'orders', 'INSERT') AS may_insert_orders,
            has_table_privilege(current_user, 'orders', 'UPDATE') AS may_update_orders,
            has_table_privilege(current_user, 'orders', 'DELETE') AS may_delete_orders
        """
    )
    return _rows(records)[0]


@mcp.tool
async def sales_summary(days: int = 30) -> dict[str, Any]:
    """Return order count, revenue, and average order value for the latest number of days."""
    if not 1 <= days <= 365:
        raise ValueError("days must be between 1 and 365")
    records = await _fetch(
        """
        SELECT
            $1::integer AS days,
            COUNT(*)::integer AS order_count,
            COALESCE(SUM(total_amount), 0)::numeric(12, 2) AS revenue,
            COALESCE(AVG(total_amount), 0)::numeric(12, 2) AS average_order_value
        FROM orders
        WHERE ordered_at >= CURRENT_DATE - $1::integer
        """,
        days,
    )
    return _rows(records)[0]


@mcp.tool
async def top_products(days: int = 30, limit: int = 5) -> dict[str, Any]:
    """Return the best-selling products by revenue for a bounded recent period."""
    if not 1 <= days <= 365:
        raise ValueError("days must be between 1 and 365")
    if not 1 <= limit <= 20:
        raise ValueError("limit must be between 1 and 20")
    records = await _fetch(
        """
        SELECT
            product_name,
            SUM(quantity)::integer AS units_sold,
            SUM(quantity * unit_price)::numeric(12, 2) AS revenue
        FROM order_items
        JOIN orders ON orders.id = order_items.order_id
        WHERE orders.ordered_at >= CURRENT_DATE - $1::integer
        GROUP BY product_name
        ORDER BY revenue DESC, product_name
        LIMIT $2::integer
        """,
        days,
        limit,
    )
    return {"days": days, "products": _rows(records)}


@mcp.tool
async def recent_orders(limit: int = 10) -> dict[str, Any]:
    """Return recent orders with customer and status information."""
    if not 1 <= limit <= 50:
        raise ValueError("limit must be between 1 and 50")
    records = await _fetch(
        """
        SELECT
            orders.id,
            customers.name AS customer,
            orders.status,
            orders.total_amount::numeric(12, 2) AS total_amount,
            orders.ordered_at::date::text AS ordered_at
        FROM orders
        JOIN customers ON customers.id = orders.customer_id
        ORDER BY orders.ordered_at DESC, orders.id DESC
        LIMIT $1::integer
        """,
        limit,
    )
    return {"orders": _rows(records)}
