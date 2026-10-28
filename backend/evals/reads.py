"""The read queries whose overhead the live eval measures (NFR-01, backend plan §14).

Fixed on purpose: the same five shapes every run, so numbers from two runs compare.
"""

from typing import Final

READ_QUERIES: Final[dict[str, str]] = {
    "point-lookup": "SELECT id, plan FROM users WHERE id = 105",
    "count": "SELECT count(*) FROM orders",
    "join": (
        "SELECT o.id, o.user_id, u.plan FROM orders o JOIN users u ON u.id = o.user_id"
        " WHERE u.id = 105 LIMIT 20"
    ),
    "group-by": "SELECT plan, count(*) AS n FROM users GROUP BY plan",
    "capped-read": "SELECT id, plan FROM users ORDER BY id LIMIT 600",
}
