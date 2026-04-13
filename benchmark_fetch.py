#!/usr/bin/env python3
"""
Benchmark Digest — Fetch leaderboard data from HuggingFace APIs.

Data sources:
1. OpenEvals/leaderboard-data — aggregated Parquet with 11 benchmarks, 100+ models
2. HuggingFace dataset leaderboard API — per-benchmark rankings (SWE-bench, MMLU-Pro, etc.)

Usage:
  python3 benchmark_fetch.py fetch              # Fetch all sources
  python3 benchmark_fetch.py fetch --source hf  # Only HuggingFace aggregated
  python3 benchmark_fetch.py fetch --source api # Only per-benchmark API
  python3 benchmark_fetch.py init               # Initialize benchmark registry
  python3 benchmark_fetch.py stats [days]       # Quick stats
"""

import argparse
import json
import os
import sys
import tempfile
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from db import get_conn, get_or_create_benchmark, upsert_score, save_snapshot

# ── Benchmark Registry ──────────────────────────────────────────────────
BENCHMARKS = [
    # From OpenEvals/leaderboard-data parquet
    {"name": "AIME 2026", "category": "reasoning", "hf_col": "aime2026_score", "url": "", "difficulty": "Math olympiad"},
    {"name": "EvasionBench", "category": "reasoning", "hf_col": "evasionBench_score", "url": "", "difficulty": "Adversarial reasoning"},
    {"name": "GPQA", "category": "reasoning", "hf_col": "gpqa_score", "url": "https://arxiv.org/abs/2311.12022", "difficulty": "PhD experts 65%"},
    {"name": "GSM8K", "category": "reasoning", "hf_col": "gsm8k_score", "url": "", "difficulty": "Grade school math (saturating)"},
    {"name": "HLE", "category": "reasoning", "hf_col": "hle_score", "url": "https://lastexam.ai", "difficulty": "Humanities expert-level"},
    {"name": "HMMT 2026", "category": "reasoning", "hf_col": "hmmt2026_score", "url": "", "difficulty": "Math competition"},
    {"name": "MMLU-Pro", "category": "reasoning", "hf_col": "mmluPro_score", "url": "", "difficulty": "Harder MMLU variant"},
    {"name": "OLM-OCR", "category": "multimodal", "hf_col": "olmOcr_score", "url": "", "difficulty": "OCR benchmark"},
    {"name": "SWE-bench Pro", "category": "code", "hf_col": "swePro_score", "url": "https://scale.com/leaderboard", "difficulty": "~23% top (private repos)"},
    {"name": "SWE-bench Verified", "category": "code", "hf_col": "sweVerified_score", "url": "https://swebench.com", "difficulty": "~70% top"},
    {"name": "TerminalBench", "category": "code", "hf_col": "terminalBench_score", "url": "", "difficulty": "Terminal/CLI tasks"},
    # From per-benchmark HF leaderboard API
    {"name": "SWE-bench Verified (API)", "category": "code", "hf_dataset": "SWE-bench/SWE-bench_Verified", "url": "https://swebench.com", "difficulty": "~70% top"},
    {"name": "MMLU-Pro (API)", "category": "reasoning", "hf_dataset": "TIGER-Lab/MMLU-Pro", "url": "", "difficulty": "Harder MMLU"},
]

PARQUET_URL = "https://huggingface.co/api/datasets/OpenEvals/leaderboard-data/parquet/default/train/0.parquet"
HF_LEADERBOARD_API = "https://huggingface.co/api/datasets/{dataset}/leaderboard"


# ── HuggingFace Aggregated Parquet ──────────────────────────────────────

def fetch_hf_parquet(conn):
    """Fetch OpenEvals/leaderboard-data parquet and store scores."""
    try:
        import pandas as pd
    except ImportError:
        print("ERROR: pandas + pyarrow required. pip install pandas pyarrow", file=sys.stderr)
        return {"status": "error", "message": "pandas not installed"}

    print("Fetching OpenEvals/leaderboard-data parquet...", file=sys.stderr)
    tmp = tempfile.NamedTemporaryFile(suffix=".parquet", delete=False)
    try:
        req = urllib.request.Request(PARQUET_URL, headers={"User-Agent": "BenchmarkDigest/2.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            tmp.write(resp.read())
        tmp.close()

        df = pd.read_parquet(tmp.name)
        print(f"  Got {len(df)} models, {len([c for c in df.columns if c.endswith('_score')])} benchmarks", file=sys.stderr)

        stats = {"source": "hf_parquet", "models": len(df), "new_scores": 0, "benchmarks_updated": 0}

        # Process each benchmark column
        for binfo in BENCHMARKS:
            col = binfo.get("hf_col")
            if not col or col not in df.columns:
                continue

            bid = get_or_create_benchmark(
                conn, binfo["name"], binfo["category"],
                url=binfo.get("url", ""),
                difficulty_note=binfo.get("difficulty", ""),
            )

            subset = df[df[col].notna()][["model_name", "provider", col]].copy()
            subset = subset.sort_values(col, ascending=False)

            for _, row in subset.iterrows():
                upsert_score(
                    conn, bid, str(row["model_name"]),
                    float(row[col]),
                    provider=str(row["provider"]) if pd.notna(row["provider"]) else "",
                    source_url="https://huggingface.co/datasets/OpenEvals/leaderboard-data",
                )
                stats["new_scores"] += 1

            # Save snapshot of top 10
            top10 = []
            for _, row in subset.head(10).iterrows():
                top10.append({
                    "model": str(row["model_name"]),
                    "score": float(row[col]),
                    "provider": str(row["provider"]) if pd.notna(row["provider"]) else "",
                })
            if top10:
                save_snapshot(conn, bid, top10)
                stats["benchmarks_updated"] += 1

        return stats

    except Exception as e:
        print(f"  ⚠️ Parquet fetch failed: {e}", file=sys.stderr)
        return {"status": "error", "message": str(e)}
    finally:
        os.unlink(tmp.name)


# ── HuggingFace Per-Benchmark Leaderboard API ──────────────────────────

def fetch_hf_api(conn):
    """Fetch per-benchmark leaderboard from HF dataset API."""
    stats = {"source": "hf_api", "new_scores": 0, "benchmarks_updated": 0, "failed": []}

    for binfo in BENCHMARKS:
        dataset = binfo.get("hf_dataset")
        if not dataset:
            continue

        url = HF_LEADERBOARD_API.format(dataset=dataset)
        print(f"  Fetching {binfo['name']} from {dataset}...", file=sys.stderr)

        try:
            req = urllib.request.Request(url, headers={"User-Agent": "BenchmarkDigest/2.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())

            if not isinstance(data, list) or not data:
                print(f"    No data for {dataset}", file=sys.stderr)
                continue

            bid = get_or_create_benchmark(
                conn, binfo["name"], binfo["category"],
                url=binfo.get("url", ""),
                difficulty_note=binfo.get("difficulty", ""),
            )

            top_models = []
            for entry in data[:30]:
                model_id = entry.get("modelId", "")
                score = entry.get("value")
                if not model_id or score is None:
                    continue

                # Extract provider from model_id (e.g., "org/model" -> "org")
                provider = model_id.split("/")[0] if "/" in model_id else ""

                upsert_score(
                    conn, bid, model_id, float(score),
                    provider=provider,
                    source_url=f"https://huggingface.co/datasets/{dataset}",
                )
                stats["new_scores"] += 1

                if len(top_models) < 10:
                    top_models.append({
                        "model": model_id,
                        "score": float(score),
                        "provider": provider,
                        "rank": entry.get("rank", 0),
                    })

            if top_models:
                save_snapshot(conn, bid, top_models)
                stats["benchmarks_updated"] += 1

            print(f"    ✅ {len(data)} entries", file=sys.stderr)

        except Exception as e:
            print(f"    ⚠️ Failed: {e}", file=sys.stderr)
            stats["failed"].append({"benchmark": binfo["name"], "error": str(e)})

    return stats


# ── Commands ────────────────────────────────────────────────────────────

def cmd_init(args):
    """Initialize benchmark registry."""
    conn = get_conn()
    for b in BENCHMARKS:
        bid = get_or_create_benchmark(
            conn, b["name"], b["category"],
            url=b.get("url", ""),
            difficulty_note=b.get("difficulty", ""),
        )
        print(f"  ✅ {b['name']} (id={bid}, category={b['category']})")
    print(f"\nTotal: {len(BENCHMARKS)} benchmarks registered.")
    conn.close()


def cmd_fetch(args):
    """Fetch latest scores from all sources."""
    conn = get_conn()
    results = []

    source = getattr(args, "source", "all")

    if source in ("all", "hf"):
        r = fetch_hf_parquet(conn)
        results.append(r)
        print(f"  Parquet: {r.get('new_scores', 0)} scores, {r.get('benchmarks_updated', 0)} benchmarks", file=sys.stderr)

    if source in ("all", "api"):
        r = fetch_hf_api(conn)
        results.append(r)
        print(f"  API: {r.get('new_scores', 0)} scores, {r.get('benchmarks_updated', 0)} benchmarks", file=sys.stderr)

    total_scores = sum(r.get("new_scores", 0) for r in results)
    total_benchmarks = sum(r.get("benchmarks_updated", 0) for r in results)

    output = {
        "total_scores": total_scores,
        "total_benchmarks_updated": total_benchmarks,
        "sources": results,
        "report": total_scores > 0,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    conn.close()


def cmd_query(args):
    """Query benchmark data for digest generation."""
    conn = get_conn()
    benchmarks = [dict(b) for b in conn.execute(
        "SELECT * FROM benchmarks WHERE is_active = 1 ORDER BY category, name"
    ).fetchall()]

    # Latest snapshots
    snapshots = {}
    for s in conn.execute("""
        SELECT ls.benchmark_id, ls.top_models_json, ls.snapshot_at, b.name
        FROM leaderboard_snapshots ls JOIN benchmarks b ON ls.benchmark_id = b.id
        WHERE ls.id IN (SELECT MAX(id) FROM leaderboard_snapshots GROUP BY benchmark_id)
    """).fetchall():
        snapshots[s[3]] = {"models": json.loads(s[1]), "snapshot_at": s[2]}

    # Recent scores
    days = getattr(args, "days", 7)
    recent = [dict(r) for r in conn.execute("""
        SELECT s.model, s.score, s.score_unit, s.provider, s.fetched_at,
               b.name as benchmark_name, b.category
        FROM scores s JOIN benchmarks b ON s.benchmark_id = b.id
        WHERE s.fetched_at > datetime('now', ?)
        ORDER BY s.fetched_at DESC
    """, (f"-{days} days",)).fetchall()]

    output = {
        "benchmarks": benchmarks,
        "snapshots": snapshots,
        "recent_scores": recent[:50],
        "total_benchmarks": len(benchmarks),
        "total_recent_scores": len(recent),
    }
    print(json.dumps(output, indent=2, ensure_ascii=False))
    conn.close()


def cmd_stats(args):
    """Quick stats."""
    conn = get_conn()
    days = getattr(args, "days", 7)

    benchmarks = conn.execute("SELECT COUNT(*) FROM benchmarks WHERE is_active = 1").fetchone()[0]
    total_scores = conn.execute("SELECT COUNT(*) FROM scores").fetchone()[0]
    recent_scores = conn.execute(
        "SELECT COUNT(*) FROM scores WHERE fetched_at > datetime('now', ?)", (f"-{days} days",)
    ).fetchone()[0]
    snapshots = conn.execute("SELECT COUNT(*) FROM leaderboard_snapshots").fetchone()[0]
    summaries = conn.execute("SELECT COUNT(*) FROM summaries").fetchone()[0]
    last_fetch = conn.execute("SELECT MAX(fetched_at) FROM scores").fetchone()[0]

    # Per-benchmark counts
    by_benchmark = conn.execute("""
        SELECT b.name, b.category, COUNT(s.id) as score_count
        FROM benchmarks b LEFT JOIN scores s ON b.id = s.benchmark_id
        WHERE b.is_active = 1
        GROUP BY b.id ORDER BY score_count DESC
    """).fetchall()

    print(json.dumps({
        "active_benchmarks": benchmarks,
        "total_scores": total_scores,
        "recent_scores": recent_scores,
        "snapshots": snapshots,
        "summaries": summaries,
        "last_fetch": last_fetch,
        "per_benchmark": [{"name": r[0], "category": r[1], "scores": r[2]} for r in by_benchmark],
    }, indent=2))
    conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Benchmark Digest — Fetch & Query (HuggingFace)")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("init", help="Initialize benchmark registry")

    f = sub.add_parser("fetch", help="Fetch latest scores")
    f.add_argument("--source", choices=["all", "hf", "api"], default="all")

    q = sub.add_parser("query", help="Query data for digest")
    q.add_argument("days", type=int, default=7, nargs="?")

    s = sub.add_parser("stats", help="Quick stats")
    s.add_argument("days", type=int, default=7, nargs="?")

    args = ap.parse_args()
    if args.cmd == "init":
        cmd_init(args)
    elif args.cmd == "fetch":
        cmd_fetch(args)
    elif args.cmd == "query":
        cmd_query(args)
    elif args.cmd == "stats":
        cmd_stats(args)
    else:
        ap.print_help()
