#!/usr/bin/env python3
"""Benchmark Digest — shared DB layer (SQLite)."""
import json, sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "benchmarks.db"


def get_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _init_tables(conn)
    return conn


def _init_tables(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS benchmarks (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        name            TEXT UNIQUE NOT NULL,
        category        TEXT NOT NULL,       -- reasoning / multimodal / agent / code / factuality / preference / domain
        url             TEXT DEFAULT '',
        description     TEXT DEFAULT '',
        difficulty_note TEXT DEFAULT '',     -- e.g. "Top models <70%", "PhD experts 65%"
        is_active       INTEGER NOT NULL DEFAULT 1,   -- 1=active, 0=dead/saturated
        added_at        TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS scores (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        benchmark_id    INTEGER NOT NULL REFERENCES benchmarks(id),
        model           TEXT NOT NULL,
        provider        TEXT DEFAULT '',     -- openai / anthropic / google / meta / etc.
        score           REAL NOT NULL,
        score_unit      TEXT DEFAULT '%',    -- % / accuracy / elo / etc.
        source_url      TEXT DEFAULT '',
        reported_at     TEXT DEFAULT '',     -- when the score was published
        fetched_at      TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(benchmark_id, model, score)
    );

    CREATE TABLE IF NOT EXISTS leaderboard_snapshots (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        benchmark_id    INTEGER NOT NULL REFERENCES benchmarks(id),
        top_models_json TEXT DEFAULT '[]',   -- JSON array of {model, score, provider}
        snapshot_at     TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS summaries (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        period          TEXT NOT NULL,        -- daily / weekly
        focus           TEXT DEFAULT 'default',
        content         TEXT NOT NULL,
        created_at      TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS subscribers (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        name            TEXT NOT NULL,
        email           TEXT DEFAULT '',
        focus           TEXT DEFAULT 'default',
        enabled         INTEGER NOT NULL DEFAULT 1,
        created_at      TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE INDEX IF NOT EXISTS idx_scores_benchmark ON scores(benchmark_id);
    CREATE INDEX IF NOT EXISTS idx_scores_model ON scores(model);
    CREATE INDEX IF NOT EXISTS idx_scores_fetched ON scores(fetched_at);
    CREATE INDEX IF NOT EXISTS idx_snapshots_benchmark ON leaderboard_snapshots(benchmark_id);
    """)
    conn.commit()


def get_or_create_benchmark(conn, name, category, url="", description="", difficulty_note="", is_active=1):
    row = conn.execute("SELECT id FROM benchmarks WHERE name = ?", (name,)).fetchone()
    if row:
        return row[0]
    conn.execute("""
        INSERT INTO benchmarks (name, category, url, description, difficulty_note, is_active)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (name, category, url, description, difficulty_note, is_active))
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def upsert_score(conn, benchmark_id, model, score, provider="", score_unit="%", source_url="", reported_at=""):
    conn.execute("""
        INSERT INTO scores (benchmark_id, model, score, provider, score_unit, source_url, reported_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(benchmark_id, model, score) DO UPDATE SET
            provider = excluded.provider,
            source_url = excluded.source_url,
            fetched_at = datetime('now')
    """, (benchmark_id, model, score, provider, score_unit, source_url, reported_at))
    conn.commit()


def save_snapshot(conn, benchmark_id, top_models):
    conn.execute("""
        INSERT INTO leaderboard_snapshots (benchmark_id, top_models_json)
        VALUES (?, ?)
    """, (benchmark_id, json.dumps(top_models, ensure_ascii=False)))
    conn.commit()


def save_summary(conn, content, period="weekly", focus="default"):
    conn.execute("""
        INSERT INTO summaries (period, focus, content) VALUES (?, ?, ?)
    """, (period, focus, content))
    conn.commit()


def query_recent_scores(conn, days=7):
    return conn.execute("""
        SELECT s.*, b.name as benchmark_name, b.category
        FROM scores s JOIN benchmarks b ON s.benchmark_id = b.id
        WHERE s.fetched_at > datetime('now', ?)
        ORDER BY s.fetched_at DESC
    """, (f"-{days} days",)).fetchall()


def query_benchmarks(conn, active_only=True):
    sql = "SELECT * FROM benchmarks"
    if active_only:
        sql += " WHERE is_active = 1"
    sql += " ORDER BY category, name"
    return conn.execute(sql).fetchall()


def query_latest_snapshots(conn):
    """Get the most recent snapshot for each benchmark."""
    return conn.execute("""
        SELECT ls.*, b.name as benchmark_name, b.category
        FROM leaderboard_snapshots ls
        JOIN benchmarks b ON ls.benchmark_id = b.id
        WHERE ls.id IN (
            SELECT MAX(id) FROM leaderboard_snapshots GROUP BY benchmark_id
        )
        ORDER BY b.category, b.name
    """).fetchall()


def get_subscribers(conn):
    return conn.execute("SELECT * FROM subscribers WHERE enabled = 1").fetchall()
