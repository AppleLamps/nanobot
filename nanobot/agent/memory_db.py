"""SQLite-backed memory index and retrieval (FTS if available)."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _hash_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", errors="ignore")).hexdigest()


def _split_into_chunks(text: str) -> list[str]:
    """
    Split markdown-ish text into stable chunks for indexing.

    Heuristic: paragraphs separated by blank lines, with trimming and size limits.
    """
    raw = re.split(r"\n\s*\n+", text.strip())
    chunks: list[str] = []
    for part in raw:
        p = part.strip()
        if not p:
            continue
        # Avoid indexing very tiny fragments.
        if len(p) < 12:
            continue
        # Keep chunks bounded for retrieval quality.
        if len(p) > 1000:
            p = p[:1000]
        chunks.append(p)
    return chunks


def _fts_query_from_text(text: str) -> str:
    # Avoid FTS query syntax injection: extract alnum tokens and OR them.
    terms = re.findall(r"[A-Za-z0-9_]{2,}", text)
    if not terms:
        return ""
    # Cap the number of terms to keep queries fast and deterministic.
    terms = terms[:16]
    return " OR ".join(terms)


@dataclass(frozen=True)
class MemoryHit:
    scope: str
    source_key: str
    content: str


class MemoryDB:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path, timeout=3.0)
        con.execute("PRAGMA journal_mode=WAL;")
        con.execute("PRAGMA synchronous=NORMAL;")
        con.execute("PRAGMA busy_timeout=3000;")
        return con

    def _ensure_schema(self) -> None:
        with self._connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_sources (
                  scope TEXT NOT NULL,
                  source TEXT NOT NULL,
                  source_key TEXT NOT NULL,
                  mtime_ns INTEGER NOT NULL,
                  updated_at TEXT NOT NULL,
                  PRIMARY KEY (scope, source, source_key)
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_entries (
                  id INTEGER PRIMARY KEY,
                  scope TEXT NOT NULL,
                  source TEXT NOT NULL,
                  source_key TEXT NOT NULL,
                  content TEXT NOT NULL,
                  content_hash TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  UNIQUE (scope, source, source_key, content_hash)
                )
                """
            )

            # Try to create FTS5. Some Python builds may not ship with it; fall back later.
            try:
                con.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS memory_entries_fts
                    USING fts5(content, scope, content='memory_entries', content_rowid='id')
                    """
                )
                con.execute(
                    """
                    CREATE TRIGGER IF NOT EXISTS memory_entries_ai AFTER INSERT ON memory_entries BEGIN
                      INSERT INTO memory_entries_fts(rowid, content, scope)
                      VALUES (new.id, new.content, new.scope);
                    END;
                    """
                )
                con.execute(
                    """
                    CREATE TRIGGER IF NOT EXISTS memory_entries_ad AFTER DELETE ON memory_entries BEGIN
                      INSERT INTO memory_entries_fts(memory_entries_fts, rowid, content, scope)
                      VALUES('delete', old.id, old.content, old.scope);
                    END;
                    """
                )
                con.execute(
                    """
                    CREATE TRIGGER IF NOT EXISTS memory_entries_au AFTER UPDATE ON memory_entries BEGIN
                      INSERT INTO memory_entries_fts(memory_entries_fts, rowid, content, scope)
                      VALUES('delete', old.id, old.content, old.scope);
                      INSERT INTO memory_entries_fts(rowid, content, scope)
                      VALUES (new.id, new.content, new.scope);
                    END;
                    """
                )
            except sqlite3.OperationalError:
                # FTS not available; ignore.
                pass

    def _get_mtime_ns(self, path: Path) -> int:
        try:
            return path.stat().st_mtime_ns if path.exists() else 0
        except Exception:
            return 0

    def ingest_file_if_changed(self, *, scope: str, source_key: str, path: Path) -> None:
        """
        Index a file under a scope. If the file hasn't changed (mtime_ns), do nothing.
        """
        mtime_ns = self._get_mtime_ns(path)
        now = _utc_now_iso()
        with self._connect() as con:
            row = con.execute(
                "SELECT mtime_ns FROM memory_sources WHERE scope=? AND source=? AND source_key=?",
                (scope, "file", source_key),
            ).fetchone()
            if row and int(row[0]) == int(mtime_ns):
                return

            # Replace all entries for this source.
            con.execute(
                "DELETE FROM memory_entries WHERE scope=? AND source=? AND source_key=?",
                (scope, "file", source_key),
            )

            text = ""
            try:
                if path.exists() and path.is_file():
                    text = path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                text = ""

            chunks = _split_into_chunks(text) if text else []
            for c in chunks:
                con.execute(
                    """
                    INSERT OR IGNORE INTO memory_entries
                      (scope, source, source_key, content, content_hash, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (scope, "file", source_key, c, _hash_text(c), now, now),
                )

            con.execute(
                """
                INSERT INTO memory_sources(scope, source, source_key, mtime_ns, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(scope, source, source_key)
                DO UPDATE SET mtime_ns=excluded.mtime_ns, updated_at=excluded.updated_at
                """,
                (scope, "file", source_key, int(mtime_ns), now),
            )

    def search(self, *, scope: str, query_text: str, limit: int = 8) -> list[MemoryHit]:
        q = _fts_query_from_text(query_text)
        if not q:
            return []

        limit = max(int(limit), 0)
        if limit <= 0:
            return []

        with self._connect() as con:
            # Prefer FTS if available.
            try:
                rows = con.execute(
                    """
                    SELECT memory_entries.source_key, memory_entries.content
                    FROM memory_entries_fts
                    JOIN memory_entries ON memory_entries_fts.rowid = memory_entries.id
                    WHERE memory_entries.scope = ?
                      AND memory_entries_fts MATCH ?
                    ORDER BY bm25(memory_entries_fts)
                    LIMIT ?
                    """,
                    (scope, q, limit),
                ).fetchall()
                return [MemoryHit(scope=scope, source_key=r[0], content=r[1]) for r in rows]
            except sqlite3.OperationalError:
                # Fall back to LIKE with tokenised OR (mirrors _fts_query_from_text).
                terms = re.findall(r"[A-Za-z0-9_]{2,}", query_text)[:16]
                if not terms:
                    return []
                where = " OR ".join(["content LIKE ?"] * len(terms))
                params: list[str | int] = [scope] + ["%" + t + "%" for t in terms] + [limit]
                rows = con.execute(
                    f"""
                    SELECT source_key, content
                    FROM memory_entries
                    WHERE scope = ? AND ({where})
                    LIMIT ?
                    """,
                    params,
                ).fetchall()
                return [MemoryHit(scope=scope, source_key=r[0], content=r[1]) for r in rows]
