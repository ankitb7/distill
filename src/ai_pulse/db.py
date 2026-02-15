import json
import sqlite3
from datetime import datetime
from pathlib import Path

from ai_pulse.models import Article, CollectedArticle, ScoreBreakdown, Source

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    normalized_url TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    author TEXT,
    source TEXT NOT NULL,
    source_id TEXT,
    content_text TEXT,
    content_length INTEGER,
    published_at TEXT,
    collected_at TEXT NOT NULL,
    tags TEXT DEFAULT '[]',
    points INTEGER,
    comment_count INTEGER,
    summary TEXT,
    is_duplicate INTEGER DEFAULT 0,
    canonical_id INTEGER REFERENCES articles(id),
    embedding BLOB
);

CREATE INDEX IF NOT EXISTS idx_articles_source ON articles(source);
CREATE INDEX IF NOT EXISTS idx_articles_collected_at ON articles(collected_at);
CREATE INDEX IF NOT EXISTS idx_articles_normalized_url ON articles(normalized_url);
CREATE INDEX IF NOT EXISTS idx_articles_is_duplicate ON articles(is_duplicate);

CREATE TABLE IF NOT EXISTS scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id INTEGER NOT NULL UNIQUE REFERENCES articles(id),
    engagement_score REAL DEFAULT 0,
    technical_depth REAL DEFAULT 0,
    novelty REAL DEFAULT 0,
    applicability REAL DEFAULT 0,
    composite_score REAL DEFAULT 0,
    reasoning TEXT,
    scored_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_scores_composite ON scores(composite_score DESC);

CREATE TABLE IF NOT EXISTS dedup_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_id INTEGER NOT NULL REFERENCES articles(id),
    duplicate_id INTEGER NOT NULL REFERENCES articles(id),
    similarity_score REAL,
    method TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(canonical_id, duplicate_id)
);

CREATE TABLE IF NOT EXISTS digests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    week_label TEXT NOT NULL UNIQUE,
    markdown TEXT,
    podcast_path TEXT,
    article_count INTEGER,
    created_at TEXT NOT NULL
);
"""


def normalize_url(url: str) -> str:
    from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

    parsed = urlparse(url)
    scheme = "https"
    netloc = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.rstrip("/") or "/"
    # Strip tracking params
    params_to_strip = {
        "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
        "ref", "source", "gi", "ocid", "fbclid", "gclid",
    }
    query_params = parse_qs(parsed.query)
    filtered = {k: v for k, v in query_params.items() if k not in params_to_strip}
    query = urlencode(filtered, doseq=True)
    return urlunparse((scheme, netloc, path, "", query, ""))


class Database:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")

    def init_schema(self):
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    def insert_article(self, article: CollectedArticle) -> int | None:
        norm_url = normalize_url(article.url)
        try:
            cursor = self.conn.execute(
                """INSERT INTO articles
                   (url, normalized_url, title, author, source, source_id,
                    published_at, collected_at, tags, points, comment_count, summary)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    article.url,
                    norm_url,
                    article.title,
                    article.author,
                    article.source.value,
                    article.source_id,
                    article.published_at.isoformat() if article.published_at else None,
                    datetime.now().isoformat(),
                    json.dumps(article.tags),
                    article.points,
                    article.comment_count,
                    article.summary,
                ),
            )
            self.conn.commit()
            return cursor.lastrowid
        except sqlite3.IntegrityError:
            # URL already exists — update engagement metrics
            self.conn.execute(
                """UPDATE articles SET points = MAX(COALESCE(points, 0), ?),
                   comment_count = MAX(COALESCE(comment_count, 0), ?)
                   WHERE normalized_url = ?""",
                (article.points or 0, article.comment_count or 0, norm_url),
            )
            self.conn.commit()
            return None

    def _row_to_article(self, row: sqlite3.Row) -> Article:
        return Article(
            id=row["id"],
            url=row["url"],
            normalized_url=row["normalized_url"],
            title=row["title"],
            author=row["author"],
            source=Source(row["source"]),
            source_id=row["source_id"],
            content_text=row["content_text"],
            content_length=row["content_length"],
            published_at=datetime.fromisoformat(row["published_at"])
            if row["published_at"]
            else None,
            collected_at=datetime.fromisoformat(row["collected_at"]),
            tags=json.loads(row["tags"]) if row["tags"] else [],
            points=row["points"],
            comment_count=row["comment_count"],
            summary=row["summary"],
            is_duplicate=bool(row["is_duplicate"]),
            canonical_id=row["canonical_id"],
        )

    def get_articles_without_content(self, limit: int = 50) -> list[Article]:
        rows = self.conn.execute(
            """SELECT * FROM articles
               WHERE content_text IS NULL AND is_duplicate = 0
               ORDER BY COALESCE(points, 0) DESC, collected_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [self._row_to_article(r) for r in rows]

    def update_content(self, article_id: int, content: str):
        self.conn.execute(
            "UPDATE articles SET content_text = ?, content_length = ? WHERE id = ?",
            (content, len(content), article_id),
        )
        self.conn.commit()

    def get_unscored_articles(
        self, min_engagement: int = 0, require_content: bool = False
    ) -> list[Article]:
        query = """SELECT a.* FROM articles a
               LEFT JOIN scores s ON a.id = s.article_id
               WHERE s.id IS NULL AND a.is_duplicate = 0
                 AND COALESCE(a.points, 0) >= ?"""
        if require_content:
            query += " AND a.content_text IS NOT NULL"
        query += " ORDER BY COALESCE(a.points, 0) DESC"
        rows = self.conn.execute(query, (min_engagement,)).fetchall()
        return [self._row_to_article(r) for r in rows]

    def insert_score(self, article_id: int, score: ScoreBreakdown):
        self.conn.execute(
            """INSERT OR REPLACE INTO scores
               (article_id, engagement_score, technical_depth, novelty,
                applicability, composite_score, reasoning, scored_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                article_id,
                score.engagement_score,
                score.technical_depth,
                score.novelty,
                score.applicability,
                score.composite_score,
                score.reasoning,
                datetime.now().isoformat(),
            ),
        )
        self.conn.commit()

    def get_top_articles(
        self, limit: int = 20, week_start: str | None = None, week_end: str | None = None
    ) -> list[tuple[Article, ScoreBreakdown]]:
        query = """
            SELECT a.*, s.engagement_score, s.technical_depth, s.novelty,
                   s.applicability, s.composite_score, s.reasoning
            FROM articles a
            LEFT JOIN scores s ON a.id = s.article_id
            WHERE a.is_duplicate = 0 AND a.content_text IS NOT NULL
        """
        params: list = []
        if week_start and week_end:
            query += " AND a.collected_at >= ? AND a.collected_at < ?"
            params.extend([week_start, week_end])
        query += " ORDER BY COALESCE(s.composite_score, 0) DESC, COALESCE(a.points, 0) DESC"
        query += " LIMIT ?"
        params.append(limit)

        rows = self.conn.execute(query, params).fetchall()
        results = []
        for r in rows:
            article = self._row_to_article(r)
            score = ScoreBreakdown(
                engagement_score=r["engagement_score"] or 0,
                technical_depth=r["technical_depth"] or 0,
                novelty=r["novelty"] or 0,
                applicability=r["applicability"] or 0,
                composite_score=r["composite_score"] or 0,
                reasoning=r["reasoning"] or "",
            )
            results.append((article, score))
        return results

    def get_all_articles(self, non_duplicate_only: bool = True) -> list[Article]:
        query = "SELECT * FROM articles"
        if non_duplicate_only:
            query += " WHERE is_duplicate = 0"
        query += " ORDER BY collected_at DESC"
        rows = self.conn.execute(query).fetchall()
        return [self._row_to_article(r) for r in rows]

    def mark_duplicate(self, duplicate_id: int, canonical_id: int, similarity: float, method: str):
        self.conn.execute(
            "UPDATE articles SET is_duplicate = 1, canonical_id = ? WHERE id = ?",
            (canonical_id, duplicate_id),
        )
        self.conn.execute(
            """INSERT OR IGNORE INTO dedup_groups
               (canonical_id, duplicate_id, similarity_score, method, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (canonical_id, duplicate_id, similarity, method, datetime.now().isoformat()),
        )
        self.conn.commit()

    def get_stats(self) -> dict:
        stats = {}
        stats["total_articles"] = self.conn.execute(
            "SELECT COUNT(*) FROM articles"
        ).fetchone()[0]
        stats["unique_articles"] = self.conn.execute(
            "SELECT COUNT(*) FROM articles WHERE is_duplicate = 0"
        ).fetchone()[0]
        stats["with_content"] = self.conn.execute(
            "SELECT COUNT(*) FROM articles WHERE content_text IS NOT NULL"
        ).fetchone()[0]
        stats["scored"] = self.conn.execute("SELECT COUNT(*) FROM scores").fetchone()[0]
        stats["duplicates"] = self.conn.execute(
            "SELECT COUNT(*) FROM articles WHERE is_duplicate = 1"
        ).fetchone()[0]

        source_rows = self.conn.execute(
            "SELECT source, COUNT(*) as cnt FROM articles GROUP BY source ORDER BY cnt DESC"
        ).fetchall()
        stats["by_source"] = {r["source"]: r["cnt"] for r in source_rows}

        return stats

    def insert_digest(self, week_label: str, markdown: str, article_count: int) -> int:
        cursor = self.conn.execute(
            """INSERT OR REPLACE INTO digests (week_label, markdown, article_count, created_at)
               VALUES (?, ?, ?, ?)""",
            (week_label, markdown, article_count, datetime.now().isoformat()),
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_manual_articles(
        self, week_start: str | None = None, week_end: str | None = None
    ) -> list[tuple[Article, ScoreBreakdown]]:
        query = """
            SELECT a.*, s.engagement_score, s.technical_depth, s.novelty,
                   s.applicability, s.composite_score, s.reasoning
            FROM articles a
            LEFT JOIN scores s ON a.id = s.article_id
            WHERE a.tags LIKE '%"manual"%' AND a.is_duplicate = 0
        """
        params: list = []
        if week_start and week_end:
            query += " AND a.collected_at >= ? AND a.collected_at < ?"
            params.extend([week_start, week_end])
        query += " ORDER BY a.collected_at DESC"
        rows = self.conn.execute(query, params).fetchall()
        results = []
        for r in rows:
            article = self._row_to_article(r)
            score = ScoreBreakdown(
                engagement_score=r["engagement_score"] or 0,
                technical_depth=r["technical_depth"] or 0,
                novelty=r["novelty"] or 0,
                applicability=r["applicability"] or 0,
                composite_score=r["composite_score"] or 0,
                reasoning=r["reasoning"] or "",
            )
            results.append((article, score))
        return results

    def get_articles_by_ids(
        self, article_ids: list[int]
    ) -> list[tuple[Article, ScoreBreakdown]]:
        if not article_ids:
            return []
        placeholders = ",".join("?" for _ in article_ids)
        query = f"""
            SELECT a.*, s.engagement_score, s.technical_depth, s.novelty,
                   s.applicability, s.composite_score, s.reasoning
            FROM articles a
            LEFT JOIN scores s ON a.id = s.article_id
            WHERE a.id IN ({placeholders})
        """
        rows = self.conn.execute(query, article_ids).fetchall()
        results = []
        for r in rows:
            article = self._row_to_article(r)
            score = ScoreBreakdown(
                engagement_score=r["engagement_score"] or 0,
                technical_depth=r["technical_depth"] or 0,
                novelty=r["novelty"] or 0,
                applicability=r["applicability"] or 0,
                composite_score=r["composite_score"] or 0,
                reasoning=r["reasoning"] or "",
            )
            results.append((article, score))
        return results

    def delete_old_articles(self, before: str) -> int:
        self.conn.execute(
            "DELETE FROM scores WHERE article_id IN "
            "(SELECT id FROM articles WHERE collected_at < ?)",
            (before,),
        )
        self.conn.execute(
            "DELETE FROM dedup_groups WHERE canonical_id IN "
            "(SELECT id FROM articles WHERE collected_at < ?) "
            "OR duplicate_id IN "
            "(SELECT id FROM articles WHERE collected_at < ?)",
            (before, before),
        )
        cursor = self.conn.execute(
            "DELETE FROM articles WHERE collected_at < ?", (before,)
        )
        self.conn.commit()
        return cursor.rowcount
