"""Knowledge and RAG (backend plan step 11).

Turns the schema scan, `schema_docs.md` and uploaded documents into chunks. The text and table tags
live in Postgres (`knowledge_chunks`); the vectors live in a `VectorStore`, computed by an
`Embedder` (ARCH-20). For an action, `retrieve` returns every chunk tagged with the affected tables
plus the top few most similar to the intent, without duplicates.
"""

import asyncio
import re
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import asyncpg
import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db import knowledge as knowledge_db
from app.db.knowledge import ChunkDraft
from app.db.models import KnowledgeChunk
from app.services import company_db
from app.services.company_schema import CompanySchema, read_schema
from app.services.embeddings import Embedder
from app.services.vectorstores import VectorItem, VectorStore

SCHEMA_SCAN_SOURCE = "schema_scan"
SCHEMA_DOCS_SOURCE = "schema_docs.md"
MAX_DOCUMENT_BYTES = 1024 * 1024
MAX_CHUNK_CHARS = 2000  # bge reads the first 512 tokens only
# 10, not the plan's 5: with the real model the business rules rank 5th to 7th for some
# users-delete intents (D-148).
SIMILAR_CHUNKS = 10
RETRIEVAL_TIMEOUT_SECONDS = 10
EMBED_BATCH_SIZE = 64
FIRST_LINE_CHARS = 200

__all__ = [
    "SCHEMA_DOCS_SOURCE",
    "SCHEMA_SCAN_SOURCE",
    "ChunkDraft",
    "DocumentError",
    "KnowledgeRef",
    "KnowledgeService",
    "RetrievalError",
    "RetrievedChunk",
    "Retrieval",
    "scan_schema",
    "split_document",
]

logger = structlog.get_logger()

_TABLE_NAME = re.compile(r"\w+")
_HEADING = re.compile(r"^(#{1,2})[ \t]+(.*?)[ \t]*$")
_TABLE_HEADING = re.compile(r"^table:[ \t]*(.*)$", re.IGNORECASE)


class DocumentError(Exception):
    """The document cannot be ingested. The message is safe to show (it holds no document text)."""


class RetrievalError(Exception):
    """Retrieval failed (embedder, store, database or the 10 s cap); the caller decides."""


# --- splitting ----------------------------------------------------------------------------------


def normalize_table_name(raw: str) -> str:
    name = raw.strip().lower().removeprefix("public.")
    if not _TABLE_NAME.fullmatch(name):
        raise DocumentError("A table tag must be a plain table name (letters, digits, underscore).")
    return name


def _pack(heading: str | None, body: str) -> list[str]:
    """One chunk, or several of at most MAX_CHUNK_CHARS split at blank lines, heading repeated."""
    whole = f"{heading}\n\n{body}" if heading else body
    if len(whole) <= MAX_CHUNK_CHARS:
        return [whole]
    prefix = f"{heading}\n\n" if heading else ""
    room = MAX_CHUNK_CHARS - len(prefix)
    pieces: list[str] = []
    for paragraph in re.split(r"\n[ \t]*\n", body):
        paragraph = paragraph.strip("\n")
        if not paragraph.strip():
            continue
        while len(paragraph) > room:  # one paragraph too long: cut at a line, else at the limit
            cut = paragraph.rfind("\n", 0, room)
            cut = cut if cut > 0 else room
            pieces.append(paragraph[:cut].rstrip())
            paragraph = paragraph[cut:].lstrip("\n")
        pieces.append(paragraph)
    parts: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + 2 + len(piece) > room:
            parts.append(current)
            current = piece
        else:
            current = f"{current}\n\n{piece}" if current else piece
    if current:
        parts.append(current)
    return [prefix + part for part in parts]


def split_document(source: str, data: bytes | str, tables: Sequence[str] = ()) -> list[ChunkDraft]:
    """Split Markdown at `#` and `##` headings into chunks.

    `## table: <name>` tags that chunk with the table; `tables` are added to every chunk. Headings
    inside code fences and `###` and deeper stay in their parent chunk. Empty sections are dropped.
    """
    if isinstance(data, bytes):
        if len(data) > MAX_DOCUMENT_BYTES:
            raise DocumentError("The document is larger than 1 MB.")
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise DocumentError("The document must be UTF-8 text.") from None
    else:
        text = data
    extra = [normalize_table_name(t) for t in tables]

    sections: list[tuple[str | None, str | None, list[str]]] = []  # heading, tag, body lines
    heading: str | None = None
    tag: str | None = None
    body: list[str] = []
    fence: str | None = None

    def close() -> None:
        sections.append((heading, tag, body))

    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            if fence is None:
                fence = marker
            elif fence == marker:
                fence = None
        match = _HEADING.match(line) if fence is None else None
        if match is None:
            body.append(line)
            continue
        close()
        heading, body, tag = line.rstrip(), [], None
        table = _TABLE_HEADING.match(match.group(2)) if len(match.group(1)) == 2 else None
        if table is not None:
            tag = normalize_table_name(table.group(1))
    close()

    drafts: list[ChunkDraft] = []
    for section_heading, section_tag, lines in sections:
        content = "\n".join(lines).strip("\n")
        if not content.strip():
            continue
        names = tuple(sorted({*extra, *([section_tag] if section_tag else [])}))
        drafts.extend(ChunkDraft(source, names, part) for part in _pack(section_heading, content))
    return drafts


# --- schema scan --------------------------------------------------------------------------------

_COLUMN_TYPES_SQL = """
SELECT c.relname::text AS table_name, a.attname::text AS column_name,
       format_type(a.atttypid, a.atttypmod) AS type, a.attnotnull AS not_null
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
ORDER BY c.relname, a.attnum
"""


def _scan_drafts(
    schema: CompanySchema, types: dict[str, list[tuple[str, str, bool]]]
) -> list[ChunkDraft]:
    drafts: list[ChunkDraft] = []
    for name in sorted(schema.tables):
        table = schema.tables[name]
        lines = [f"## table: {name}", "", f"Schema of table {name} (read from the database).", ""]
        lines.append("Columns:")
        for column, type_, not_null in types.get(name, []):
            lines.append(f"- {column}: {type_}, {'not null' if not_null else 'nullable'}")
        if table.pk_columns:
            lines.append(f"Primary key: {', '.join(table.pk_columns)}")
        out = [fk for fk in schema.foreign_keys if fk.child == name]
        if out:
            lines.append("References (foreign keys out):")
            lines.extend(
                f"- {', '.join(fk.child_columns)} -> {fk.parent}({', '.join(fk.parent_columns)}),"
                f" on delete: {fk.on_delete.replace('_', ' ')}"
                for fk in out
            )
        incoming = [fk for fk in schema.foreign_keys if fk.parent == name]
        if incoming:
            lines.append("Referenced by (foreign keys in):")
            lines.extend(
                f"- {fk.child}({', '.join(fk.child_columns)}), on delete:"
                f" {fk.on_delete.replace('_', ' ')}"
                for fk in incoming
            )
        drafts.append(ChunkDraft(SCHEMA_SCAN_SOURCE, (name,), "\n".join(lines)))
    return drafts


async def scan_schema(settings: Settings) -> list[ChunkDraft]:
    """One chunk per `public` table of the company database, read as the reader role."""
    async with company_db.connect_reader(settings) as conn:
        schema = await read_schema(conn)
        types: dict[str, list[tuple[str, str, bool]]] = {}
        for row in await _fetch_types(conn):
            types.setdefault(row["table_name"], []).append(
                (row["column_name"], row["type"], row["not_null"])
            )
    return _scan_drafts(schema, types)


async def _fetch_types(conn: asyncpg.Connection) -> list[asyncpg.Record]:
    return await conn.fetch(_COLUMN_TYPES_SQL)


# --- retrieval ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class KnowledgeRef:
    """What an action stores in `knowledge_used`: where a chunk came from, never its full text."""

    chunk_id: str
    source: str
    table_names: list[str]
    first_line: str
    match: Literal["table", "similar"]
    score: float | None


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    source: str
    table_names: list[str]
    content: str
    match: Literal["table", "similar"]
    score: float | None


@dataclass(frozen=True)
class Retrieval:
    chunks: list[RetrievedChunk]
    refs: list[KnowledgeRef]
    duration_ms: int


def _first_line(content: str) -> str:
    line = next((ln.strip() for ln in content.splitlines() if ln.strip()), "")
    return line[:FIRST_LINE_CHARS]


def _retrieved(
    chunk: KnowledgeChunk, match: Literal["table", "similar"], score: float | None
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=str(chunk.id),
        source=chunk.source,
        table_names=list(chunk.table_names),
        content=chunk.content,
        match=match,
        score=score,
    )


class KnowledgeService:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        embedder: Embedder,
        store: VectorStore,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._embedder = embedder
        self._store = store
        # One writer at a time in this process: a second ingest must not delete the chunks whose
        # vectors the first is still writing. (Several processes: reindex repairs a lost race.)
        self._write_lock = asyncio.Lock()

    async def _embed_all(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), EMBED_BATCH_SIZE):
            vectors.extend(await self._embedder.embed(texts[start : start + EMBED_BATCH_SIZE]))
        return vectors

    async def ingest_drafts(self, source: str, drafts: Sequence[ChunkDraft]) -> int:
        """Replace one source's chunks and vectors. Returns the new chunk count.

        Embeds first, outside any transaction. If the vector upsert then fails, the chunks exist
        without vectors and `reindex` repairs them.
        """
        vectors = await self._embed_all([d.content for d in drafts])
        async with self._write_lock:
            async with self._sessionmaker() as session, session.begin():
                old_ids, chunks = await knowledge_db.replace_source(session, source, drafts)
            await self._store.delete(old_ids)
            await self._store.upsert(
                [
                    VectorItem(chunk.id, vector, chunk.source, tuple(chunk.table_names))
                    for chunk, vector in zip(chunks, vectors, strict=True)
                ]
            )
        logger.info("knowledge_ingested", source_kind=_kind(source), chunks=len(chunks))
        return len(chunks)

    async def ingest(self, source: str, data: bytes, tables: Sequence[str] = ()) -> int:
        return await self.ingest_drafts(source, split_document(source, data, tables))

    async def scan(self, settings: Settings) -> int:
        return await self.ingest_drafts(SCHEMA_SCAN_SOURCE, await scan_schema(settings))

    async def delete_source(self, source: str) -> int:
        async with self._write_lock:
            async with self._sessionmaker() as session, session.begin():
                ids = await knowledge_db.delete_source(session, source)
            await self._store.delete(ids)
        return len(ids)

    async def reindex(self) -> int:
        """Rebuild every vector from the chunk text in Postgres (after a swap or a failure)."""
        async with self._sessionmaker() as session:
            chunks = await knowledge_db.all_chunks(session)
        await self._store.clear()
        for start in range(0, len(chunks), EMBED_BATCH_SIZE):
            batch = chunks[start : start + EMBED_BATCH_SIZE]
            vectors = await self._embedder.embed([c.content for c in batch])
            await self._store.upsert(
                [
                    VectorItem(c.id, v, c.source, tuple(c.table_names))
                    for c, v in zip(batch, vectors, strict=True)
                ]
            )
        return len(chunks)

    async def retrieve(
        self, intent: str, tables: Sequence[str], k: int = SIMILAR_CHUNKS
    ) -> Retrieval:
        """Every chunk tagged with these tables, then the `k` most similar to the intent."""
        started = time.perf_counter()
        # Not validated like a tag: the names come from the classifier and are query parameters.
        wanted = sorted({t.strip().lower().removeprefix("public.") for t in tables})
        try:
            async with asyncio.timeout(RETRIEVAL_TIMEOUT_SECONDS):
                async with self._sessionmaker() as session:
                    tagged = await knowledge_db.chunks_for_tables(session, wanted)
                [vector] = await self._embedder.embed([intent])
                hits = await self._store.search(vector, k)
                async with self._sessionmaker() as session:
                    found = await knowledge_db.chunks_by_ids(session, [h.chunk_id for h in hits])
        except Exception as exc:
            logger.error("retrieve_failed", reason_code=type(exc).__name__)
            raise RetrievalError(type(exc).__name__) from None
        scores = {h.chunk_id: h.score for h in hits}
        by_id: dict[uuid.UUID, KnowledgeChunk] = {c.id: c for c in found}  # stale IDs are gone
        chunks = [_retrieved(c, "table", scores.get(c.id)) for c in tagged]
        seen = {c.id for c in tagged}
        for hit in hits:  # the store's order is best first
            chunk = by_id.get(hit.chunk_id)
            if chunk is not None and chunk.id not in seen:
                seen.add(chunk.id)
                chunks.append(_retrieved(chunk, "similar", hit.score))
        duration_ms = round((time.perf_counter() - started) * 1000)
        logger.info(
            "retrieve_done",
            tagged=len(tagged),
            similar=len(chunks) - len(tagged),
            duration_ms=duration_ms,
        )
        refs = [
            KnowledgeRef(
                chunk_id=c.chunk_id,
                source=c.source,
                table_names=c.table_names,
                first_line=_first_line(c.content),
                match=c.match,
                score=c.score,
            )
            for c in chunks
        ]
        return Retrieval(chunks=chunks, refs=refs, duration_ms=duration_ms)


def _kind(source: str) -> str:
    """Log the kind of source, not an uploaded file's name."""
    return source if source in (SCHEMA_SCAN_SOURCE, SCHEMA_DOCS_SOURCE) else "upload"
