"""BE-12: schema_docs.md has one `## table:` section per table in schema.sql, plus business rules.

Reads the files as text; the backend never imports demo_company (D-047).
"""

import re

from tests.conftest import DEMO_COMPANY_DIR


def table_headings() -> list[str]:
    text = (DEMO_COMPANY_DIR / "schema_docs.md").read_text(encoding="utf-8")
    return re.findall(r"^## table: (\w+)\s*$", text, flags=re.MULTILINE)


def schema_tables() -> set[str]:
    text = (DEMO_COMPANY_DIR / "schema.sql").read_text(encoding="utf-8")
    return set(re.findall(r"^CREATE TABLE (\w+) \(", text, flags=re.MULTILINE))


def test_every_company_table_has_one_section() -> None:
    headings = table_headings()
    assert len(headings) == len(set(headings))
    assert set(headings) == schema_tables()
    assert len(headings) == 11


def test_business_rules_name_paying_and_enterprise() -> None:
    text = (DEMO_COMPANY_DIR / "schema_docs.md").read_text(encoding="utf-8")
    rules = text.split("\n## business rules\n", 1)[1]
    assert "paying customer" in rules
    assert "`pro` or `enterprise`" in rules
    assert "Enterprise accounts" in rules and "must never be deleted" in rules
