"""Step 11: splitting Markdown into knowledge chunks (BE-59, BE-60)."""

import re
from pathlib import Path

import pytest

from app.services.knowledge import MAX_CHUNK_CHARS, DocumentError, split_document
from tests.conftest import DEMO_COMPANY_DIR

DOC = """Intro text before any heading.

# Company notes

General notes.

## table: Users

Users are customers.

### Details

Stays in the users chunk.

## table: public.orders

Orders belong to users.

## Business rules

- Never delete paying customers.
"""


def split(text: str, tables: tuple[str, ...] = ()) -> list:
    return split_document("doc.md", text, tables)


def test_splits_at_headings_and_tags_table_sections() -> None:
    chunks = split(DOC)
    assert [c.table_names for c in chunks] == [(), (), ("users",), ("orders",), ()]
    assert [c.content.splitlines()[0] for c in chunks] == [
        "Intro text before any heading.",
        "# Company notes",
        "## table: Users",
        "## table: public.orders",
        "## Business rules",
    ]
    assert {c.source for c in chunks} == {"doc.md"}


def test_a_third_level_heading_stays_in_its_parent_chunk() -> None:
    users = split(DOC)[2]
    assert "### Details" in users.content
    assert "Stays in the users chunk." in users.content


def test_the_preamble_is_its_own_untagged_chunk() -> None:
    first = split(DOC)[0]
    assert first.content == "Intro text before any heading."
    assert first.table_names == ()


def test_a_heading_inside_a_code_fence_does_not_split() -> None:
    text = "## table: users\n\nExample:\n\n```md\n## table: orders\n# Title\n```\n\nAfter.\n"
    [chunk] = split(text)
    assert chunk.table_names == ("users",)
    assert "## table: orders" in chunk.content
    assert "After." in chunk.content


def test_a_tilde_fence_works_too() -> None:
    [chunk] = split("## A\n\n~~~\n## B\n~~~\n")
    assert "## B" in chunk.content


def test_empty_sections_are_dropped() -> None:
    chunks = split("## table: users\n\n## table: orders\n\nText.\n\n## Empty\n   \n")
    assert [c.table_names for c in chunks] == [("orders",)]


def test_an_untagged_heading_gives_an_untagged_chunk() -> None:
    [chunk] = split("## Something else\n\nText.")
    assert chunk.table_names == ()


def test_upload_tags_are_joined_with_the_heading_tag_on_every_chunk() -> None:
    chunks = split(DOC, tables=("Orders", "public.Invoices"))
    assert chunks[0].table_names == ("invoices", "orders")
    assert chunks[2].table_names == ("invoices", "orders", "users")
    assert chunks[3].table_names == ("invoices", "orders")


def test_a_long_section_is_split_at_blank_lines_with_its_heading_repeated() -> None:
    paragraphs = [f"Paragraph {i}: " + "word " * 80 for i in range(12)]
    chunks = split("## table: users\n\n" + "\n\n".join(paragraphs))
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk.content) <= MAX_CHUNK_CHARS
        assert chunk.content.startswith("## table: users\n\n")
        assert chunk.table_names == ("users",)
    joined = "\n\n".join(c.content.removeprefix("## table: users\n\n") for c in chunks)
    assert joined.replace("\n\n", "\n") == "\n".join(paragraphs)


def test_one_paragraph_longer_than_the_limit_is_still_cut() -> None:
    chunks = split("## Long\n\n" + "x" * (MAX_CHUNK_CHARS * 2 + 10))
    assert len(chunks) == 3
    assert all(len(c.content) <= MAX_CHUNK_CHARS for c in chunks)


def test_crlf_and_a_byte_order_mark_are_handled() -> None:
    data = ("﻿" + DOC.replace("\n", "\r\n")).encode("utf-8")
    assert split_document("doc.md", data) == split(DOC)


def test_non_utf8_input_is_refused() -> None:
    with pytest.raises(DocumentError, match="UTF-8"):
        split_document("doc.md", "café".encode("latin-1"))


def test_input_over_one_megabyte_is_refused() -> None:
    with pytest.raises(DocumentError, match="1 MB"):
        split_document("doc.md", b"a" * (1024 * 1024 + 1))
    assert split_document("doc.md", b"a" * (1024 * 1024))


@pytest.mark.parametrize("name", ["us ers", "users;", "a.b", "", "users-1", "ord/ers"])
def test_a_bad_table_tag_refuses_the_file(name: str) -> None:
    with pytest.raises(DocumentError, match="table tag"):
        split(f"## table: {name}\n\nText.")


def test_a_bad_upload_tag_refuses_the_file() -> None:
    with pytest.raises(DocumentError, match="table tag"):
        split("## A\n\nText.", tables=("bad name",))


def test_the_error_does_not_hold_the_document_text() -> None:
    with pytest.raises(DocumentError) as raised:
        split("## table: secret@example.com\n\nText.")
    assert "secret" not in str(raised.value)


def test_the_real_schema_docs_give_eleven_tagged_chunks_and_the_business_rules() -> None:
    data = Path(DEMO_COMPANY_DIR / "schema_docs.md").read_bytes()
    chunks = split_document("schema_docs.md", data)
    tagged = [c for c in chunks if c.table_names]
    assert len(tagged) == 11
    assert all(len(c.table_names) == 1 for c in tagged)
    schema_sql = (DEMO_COMPANY_DIR / "schema.sql").read_text(encoding="utf-8")
    tables = set(re.findall(r"CREATE TABLE (\w+) \(", schema_sql))
    assert {c.table_names[0] for c in tagged} == tables
    [rules] = [c for c in chunks if c.content.startswith("## business rules")]
    assert rules.table_names == ()
    assert "paying customer" in rules.content
    assert "Enterprise accounts" in rules.content
    assert "must never be deleted" in rules.content
