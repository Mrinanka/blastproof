"""Deterministic demo data for the fake company database (backend plan §8 step 3, D-017, D-052).

The same seed always gives the same data:
- Every timestamp is relative to ANCHOR (2026-10-01), never to the real clock.
- Every value that changes a count (plan, last login, how many orders...) comes from
  random.Random.random(), the one generator method Python promises to keep stable across versions.
  Faker only fills cosmetic text (names, phones, company names), so a Faker upgrade cannot move
  a count.
- The demo's headline numbers are fixed by construction, not by chance: 48,213 users last logged in
  more than 6 months before the anchor, 9 of them pro and 3 enterprise (12 paying). Every last
  login is at least 14 days away from that 6-month cutoff, so time of day, "180 days" instead of
  "6 months", or rebasing by whole days never changes who counts as inactive.
"""

import random
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from faker import Faker

SEED = 20261001
ANCHOR = datetime(2026, 10, 1, tzinfo=UTC)
ANCHOR_DATE: date = ANCHOR.date()
INACTIVE_CUTOFF = datetime(2026, 4, 1, tzinfo=UTC)  # 6 months before ANCHOR
CUTOFF_MARGIN = timedelta(days=14)
EARLIEST = datetime(2023, 1, 1, tzinfo=UTC)

# (plan, active, count). Inactive = last login before INACTIVE_CUTOFF.
USER_MIX: list[tuple[str, bool, int]] = [
    ("free", False, 48_201),
    ("pro", False, 9),
    ("enterprise", False, 3),
    ("free", True, 1_187),
    ("pro", True, 450),
    ("enterprise", True, 150),
]
PAYING = ("pro", "enterprise")
MONTHLY_PRICE_CENTS = {"pro": 2_900, "enterprise": 49_900}

N_ORGANIZATIONS = 2_000
N_PRODUCTS = 40
MAX_PAYMENTS_PER_SUBSCRIPTION = 12

COUNTRIES = ["US", "GB", "DE", "FR", "IN", "CA", "AU", "BR", "NL", "ES", "SE", "JP"]
INDUSTRIES = ["software", "retail", "finance", "health", "education", "media", "logistics"]
PRODUCT_ADJECTIVES = ["Pro", "Team", "Extra", "Premium", "Starter", "Annual", "Priority", "Bulk"]
PRODUCT_NOUNS = ["Seats", "Storage", "Support", "Credits", "Exports", "Hoodie", "Mug", "Training"]
TICKET_SUBJECTS = [
    "Cannot log in",
    "Billing question",
    "Export is failing",
    "Feature request",
    "Invoice copy needed",
    "Slow dashboard",
    "Cancel my plan",
    "API rate limits",
]
NOTE_BODIES = [
    "Asked about upgrading to pro.",
    "Prefers email over phone.",
    "Reported a bug in exports; fixed.",
    "Renewal call scheduled.",
    "Requested an invoice copy.",
    "Happy with support response time.",
    "Considering moving to a competitor.",
    "Onboarding call completed.",
]
FEATURE_FLAG_KEYS = [
    "new_dashboard",
    "dark_mode",
    "bulk_export",
    "ai_summaries",
    "sso_login",
    "audit_log_v2",
    "faster_search",
    "usage_alerts",
    "team_invites",
    "csv_import",
    "billing_portal",
    "webhooks_v2",
    "mobile_push",
    "custom_roles",
    "beta_reports",
    "inline_comments",
    "two_factor_prompt",
    "slack_integration",
    "data_retention_ui",
    "pricing_page_b",
    "onboarding_tour",
    "api_keys_v2",
    "status_page",
    "referral_program",
    "legacy_editor_off",
]


class Rng:
    """Draws only through random.random(), whose sequence Python keeps stable across versions."""

    def __init__(self, seed: int) -> None:
        self._random = random.Random(seed)

    def unit(self) -> float:
        return self._random.random()

    def int(self, low: int, high: int) -> int:
        """An integer in [low, high], both inclusive."""
        return low + int(self.unit() * (high - low + 1))

    def pick[T](self, items: list[T]) -> T:
        return items[int(self.unit() * len(items))]

    def chance(self, p: float) -> bool:
        return self.unit() < p

    def between(self, start: datetime, end: datetime) -> datetime:
        seconds = int((end - start).total_seconds() * self.unit())
        return start + timedelta(seconds=seconds)

    def shuffle(self, items: list[Any]) -> None:
        for i in range(len(items) - 1, 0, -1):
            j = int(self.unit() * (i + 1))
            items[i], items[j] = items[j], items[i]


@dataclass
class Table:
    columns: tuple[str, ...]
    rows: list[tuple[Any, ...]] = field(default_factory=list)


# Load order: parents before children.
TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "organizations": ("id", "name", "industry", "created_at"),
    "users": (
        "id",
        "organization_id",
        "email",
        "full_name",
        "phone",
        "country",
        "plan",
        "created_at",
        "last_login_at",
    ),
    "subscriptions": (
        "id",
        "user_id",
        "plan",
        "status",
        "monthly_price_cents",
        "started_at",
        "cancelled_at",
    ),
    "payments": ("id", "user_id", "amount_cents", "currency", "status", "paid_at"),
    "invoices": ("id", "user_id", "number", "amount_cents", "status", "issued_at", "due_date"),
    "products": ("id", "sku", "name", "price_cents", "active", "created_at"),
    "orders": ("id", "user_id", "status", "total_cents", "created_at"),
    "order_items": ("id", "order_id", "product_id", "quantity", "unit_price_cents"),
    "support_tickets": (
        "id",
        "user_id",
        "subject",
        "status",
        "priority",
        "created_at",
        "closed_at",
    ),
    "feature_flags": ("id", "key", "description", "enabled", "rollout_percent", "updated_at"),
    "customer_notes": ("id", "user_id", "body", "created_at"),
}


def _email(name: str, user_id: int) -> str:
    local = re.sub(r"[^a-z]+", ".", name.lower()).strip(".")
    return f"{local}.{user_id}@example.com"


def generate() -> dict[str, Table]:
    rng = Rng(SEED)
    fake = Faker("en_US")
    fake.seed_instance(SEED)
    data = {name: Table(columns) for name, columns in TABLE_COLUMNS.items()}

    for org_id in range(1, N_ORGANIZATIONS + 1):
        created = rng.between(EARLIEST, ANCHOR - timedelta(days=30))
        data["organizations"].rows.append((org_id, fake.company(), rng.pick(INDUSTRIES), created))

    products: list[int] = []  # price per product id - 1
    for product_id in range(1, N_PRODUCTS + 1):
        price = rng.int(500, 20_000)
        products.append(price)
        name = f"{rng.pick(PRODUCT_ADJECTIVES)} {rng.pick(PRODUCT_NOUNS)}"
        created = rng.between(EARLIEST, EARLIEST + timedelta(days=180))
        data["products"].rows.append(
            (product_id, f"SKU-{product_id:04d}", name, price, rng.chance(0.85), created)
        )

    categories = [(plan, active) for plan, active, count in USER_MIX for _ in range(count)]
    rng.shuffle(categories)

    ids = {"subscriptions": 0, "payments": 0, "invoices": 0, "orders": 0, "order_items": 0}
    ids |= {"support_tickets": 0, "customer_notes": 0}

    def next_id(table: str) -> int:
        ids[table] += 1
        return ids[table]

    org_chance = {"free": 0.15, "pro": 0.6, "enterprise": 1.0}
    for user_id, (plan, active) in enumerate(categories, start=1):
        if active:
            last_login = rng.between(INACTIVE_CUTOFF + CUTOFF_MARGIN, ANCHOR - timedelta(hours=1))
        else:
            last_login = rng.between(EARLIEST + timedelta(days=30), INACTIVE_CUTOFF - CUTOFF_MARGIN)
        created = rng.between(EARLIEST, last_login)
        org_id = rng.int(1, N_ORGANIZATIONS) if rng.chance(org_chance[plan]) else None
        name = fake.name()
        phone = fake.phone_number() if rng.chance(0.7) else None
        data["users"].rows.append(
            (
                user_id,
                org_id,
                _email(name, user_id),
                name,
                phone,
                rng.pick(COUNTRIES),
                plan,
                created,
                last_login,
            )
        )

        # Subscriptions: every paying user has an active one (including the 12 inactive ones,
        # the demo's trap); about 3% of free users once paid for pro and cancelled.
        subscription: tuple[str, datetime, datetime | None] | None = None
        if plan in PAYING:
            subscription = (plan, rng.between(created, last_login), None)
        elif rng.chance(0.03):
            started = rng.between(created, last_login)
            subscription = ("pro", started, rng.between(started, last_login))
        if subscription is not None:
            sub_plan, started, cancelled = subscription
            price = MONTHLY_PRICE_CENTS[sub_plan]
            status = "active" if cancelled is None else "cancelled"
            data["subscriptions"].rows.append(
                (next_id("subscriptions"), user_id, sub_plan, status, price, started, cancelled)
            )
            end = cancelled or ANCHOR
            due = [started + timedelta(days=30 * k) for k in range(0, 200)]
            due = [d for d in due if d < end][-MAX_PAYMENTS_PER_SUBSCRIPTION:]
            for paid_at in due:
                succeeded = rng.chance(0.97)
                data["payments"].rows.append(
                    (
                        next_id("payments"),
                        user_id,
                        price,
                        "USD",
                        "succeeded" if succeeded else "failed",
                        paid_at,
                    )
                )
                invoice_id = next_id("invoices")
                if not succeeded:
                    invoice_status = "void"
                elif paid_at > ANCHOR - timedelta(days=30) and rng.chance(0.3):
                    invoice_status = "open"
                else:
                    invoice_status = "paid"
                data["invoices"].rows.append(
                    (
                        invoice_id,
                        user_id,
                        f"INV-{invoice_id:07d}",
                        price,
                        invoice_status,
                        paid_at,
                        (paid_at + timedelta(days=14)).date(),
                    )
                )

        n_orders = rng.int(1, 5) if plan in PAYING else rng.int(0, 2)
        for _ in range(n_orders):
            order_id = next_id("orders")
            order_created = rng.between(created, last_login)
            total = 0
            for _ in range(rng.int(1, 4)):
                product_id = rng.int(1, N_PRODUCTS)
                quantity = rng.int(1, 3)
                unit_price = products[product_id - 1]
                total += quantity * unit_price
                data["order_items"].rows.append(
                    (next_id("order_items"), order_id, product_id, quantity, unit_price)
                )
            roll = rng.unit()
            status = "completed" if roll < 0.9 else "refunded" if roll < 0.95 else "cancelled"
            data["orders"].rows.append((order_id, user_id, status, total, order_created))

        if rng.chance(0.12):
            for _ in range(rng.int(1, 3)):
                opened = rng.between(created, last_login)
                if opened < ANCHOR - timedelta(days=30):
                    status = "closed"
                else:
                    status = rng.pick(["open", "pending", "closed"])
                closed_at = None
                if status == "closed":
                    closed_at = min(opened + timedelta(days=rng.int(1, 10)), ANCHOR)
                data["support_tickets"].rows.append(
                    (
                        next_id("support_tickets"),
                        user_id,
                        rng.pick(TICKET_SUBJECTS),
                        status,
                        rng.pick(["low", "normal", "high"]),
                        opened,
                        closed_at,
                    )
                )

        if rng.chance(0.5 if plan in PAYING else 0.08):
            for _ in range(rng.int(1, 2)):
                data["customer_notes"].rows.append(
                    (
                        next_id("customer_notes"),
                        user_id,
                        rng.pick(NOTE_BODIES),
                        rng.between(created, last_login),
                    )
                )

    for flag_id, key in enumerate(FEATURE_FLAG_KEYS, start=1):
        data["feature_flags"].rows.append(
            (
                flag_id,
                key,
                f"Turns on {key.replace('_', ' ')}.",
                rng.chance(0.6),
                rng.pick([0, 10, 25, 50, 100]),
                rng.between(ANCHOR - timedelta(days=200), ANCHOR - timedelta(days=1)),
            )
        )

    return data
