#!/usr/bin/env python3
"""Benchmark Digest — Fetch leaderboard data from multiple sources."""
import argparse, json, re, sys, urllib.request, urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from db import get_conn, get_or_create_benchmark, upsert_score, save_snapshot, get_subscribers, save_summary

# ── Benchmark Registry ──────────────────────────────────────────────────
BENCHMARKS = [
    # Reasoning
    {"name": "LiveBench", "category": "reasoning", "url": "https://livebench.ai", "difficulty": "Top models <70%"},
    {"name": "ARC-AGI-2", "category": "reasoning", "url": "https://arcprize.org/leaderboard", "difficulty": "Pure LLMs score 0%"},
    {"name": "GPQA-Diamond", "category": "reasoning", "url": "https://arxiv.org/abs/2311.12022", "difficulty": "PhD experts 65%"},
    {"name": "HLE", "category": "reasoning", "url": "https://lastexam.ai", "difficulty": "Humanities expert-level"},
    # Multimodal
    {"name": "MMMU", "category": "multimodal", "url": "https://mmmu-benchmark.github.io", "difficulty": "University-level multimodal"},
    {"name": "MMMU-Pro", "category": "multimodal", "url": "https://mmmu-benchmark.github.io", "difficulty": "Harder MMMU variant"},
    {"name": "MathVista", "category": "multimodal", "url": "https://mathvista.github.io", "difficulty": "Visual math reasoning"},
    {"name": "Video-MME", "category": "multimodal", "url": "https://video-mme.github.io", "difficulty": "Video understanding"},
    {"name": "EgoSchema", "category": "multimodal", "url": "https://egoschema.github.io", "difficulty": "Egocentric video"},
    {"name": "ChartQA", "category": "multimodal", "url": "", "difficulty": "Chart understanding"},
    # Agent
    {"name": "GAIA", "category": "agent", "url": "https://huggingface.co/spaces/gaia-benchmark/leaderboard", "difficulty": "General agent ~50%"},
    {"name": "BrowseComp", "category": "agent", "url": "", "difficulty": "Basic 1.9%, specialized 51%"},
    {"name": "Tau-bench", "category": "agent", "url": "", "difficulty": "Tool-use reliability"},
    {"name": "MM-BrowseComp", "category": "agent", "url": "https://arxiv.org/abs/2508.13186", "difficulty": "o3=29%"},
    # Code
    {"name": "SWE-bench Verified", "category": "code", "url": "https://swebench.com", "difficulty": "~70% top"},
    {"name": "SWE-bench Pro", "category": "code", "url": "https://scale.com/leaderboard", "difficulty": "~23% top (private repos)"},
    {"name": "SciCode", "category": "code", "url": "", "difficulty": "Scientific coding"},
    # Factuality
    {"name": "SimpleQA", "category": "factuality", "url": "", "difficulty": "Factual recall"},
    {"name": "HHEM", "category": "factuality", "url": "", "difficulty": "Hallucination quantification"},
    # Human preference
    {"name": "LMArena", "category": "preference", "url": "https://lmarena.ai", "difficulty": "Human blind preference + style control"},
    {"name": "Scale SEAL", "category": "domain", "url": "", "difficulty": "Legal/finance professional"},
]

# ── Source Fetchers ─────────────────────────────────────────────────────

def fetch_url(url, timeout=30):
    """Simple URL fetch returning text."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "BenchmarkDigest/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  ⚠️ Failed to fetch {url}: {e}", file=sys.stderr)
        return None


def fetch_livebench():
    """Fetch LiveBench scores from livebench.ai."""
    scores = []
    html = fetch_url("https://livebench.ai/api/leaderboard")
    if not html:
        return scores
    try:
        data = json.loads(html)
        for entry in data.get("leaderboard", data) if isinstance(data, dict) else data:
            if isinstance(entry, dict):
                model = entry.get("model", entry.get("name", ""))
                score = entry.get("score", entry.get("average", 0))
                if model and score:
                    scores.append({"model": model, "score": float(score), "provider": ""})
    except (json.JSONDecodeError, TypeError):
        # Try regex fallback
        pass
    return scores[:20]


def fetch_lmarena():
    """Fetch LMArena / Chatbot Arena Elo ratings."""
    scores = []
    html = fetch_url("https://lmarena.ai/api/v1/leaderboard")
    if not html:
        return scores
    try:
        data = json.loads(html)
        for entry in data if isinstance(data, list) else data.get("data", []):
            if isinstance(entry, dict):
                model = entry.get("model", entry.get("name", ""))
                elo = entry.get("elo", entry.get("rating", 0))
                if model and elo:
                    scores.append({"model": model, "score": float(elo), "provider": ""})
    except (json.JSONDecodeError, TypeError):
        pass
    return scores[:20]


def fetch_gaia():
    """Fetch GAIA benchmark from HuggingFace spaces."""
    scores = []
    # GAIA leaderboard is on HuggingFace, harder to scrape
    # We'll rely on the digest LLM to pull latest numbers
    return scores


# Map benchmark names to fetchers
FETCHERS = {
    "LiveBench": fetch_livebench,
    "LMArena": fetch_lmarena,
    "GAIA": fetch_gaia,
}


# ── Commands ────────────────────────────────────────────────────────────

def cmd_init(args):
    """Initialize benchmark registry in DB."""
    conn = get_conn()
    for b in BENCHMARKS:
        bid = get_or_create_benchmark(
            conn, b["name"], b["category"],
            url=b.get("url", ""),
            description=b.get("description", ""),
            difficulty_note=b.get("difficulty", ""),
        )
        print(f"  ✅ {b['name']} (id={bid}, category={b['category']})")
    print(f"\n总计: {len(BENCHMARKS)} benchmarks registered.")
    conn.close()


def cmd_fetch(args):
    """Fetch latest scores from available sources."""
    conn = get_conn()
    total_new = 0

    for bname, fetcher in FETCHERS.items():
        print(f"  Fetching {bname}...", file=sys.stderr)
        scores = fetcher()
        if scores:
            row = conn.execute("SELECT id FROM benchmarks WHERE name = ?", (bname,)).fetchone()
            if row:
                bid = row[0]
                for s in scores:
                    upsert_score(conn, bid, s["model"], s["score"],
                                provider=s.get("provider", ""),
                                score_unit=s.get("unit", "%"))
                    total_new += 1
                # Save snapshot
                save_snapshot(conn, bid, scores[:10])
            print(f"  ✅ {bname}: {len(scores)} scores", file=sys.stderr)
        else:
            print(f"  ⚠️ {bname}: no data", file=sys.stderr)

    result = {"fetched_benchmarks": len(FETCHERS), "total_scores": total_new}
    print(json.dumps(result))
    conn.close()


def cmd_query(args):
    """Query benchmark data for digest generation."""
    conn = get_conn()
    benchmarks = [dict(b) for b in conn.execute("SELECT * FROM benchmarks WHERE is_active = 1 ORDER BY category, name").fetchall()]

    # Get latest snapshots
    snapshots = {}
    for s in conn.execute("""
        SELECT ls.benchmark_id, ls.top_models_json, ls.snapshot_at, b.name
        FROM leaderboard_snapshots ls JOIN benchmarks b ON ls.benchmark_id = b.id
        WHERE ls.id IN (SELECT MAX(id) FROM leaderboard_snapshots GROUP BY benchmark_id)
    """).fetchall():
        snapshots[s[3]] = {"models": json.loads(s[1]), "snapshot_at": s[2]}

    # Get recent score changes
    recent = [dict(r) for r in conn.execute("""
        SELECT s.model, s.score, s.score_unit, s.reported_at, s.fetched_at,
               b.name as benchmark_name, b.category
        FROM scores s JOIN benchmarks b ON s.benchmark_id = b.id
        WHERE s.fetched_at > datetime('now', ?)
        ORDER BY s.fetched_at DESC
    """, (f"-{args.days} days",)).fetchall()]

    output = {
        "benchmarks": benchmarks,
        "snapshots": snapshots,
        "recent_scores": recent,
        "total_benchmarks": len(benchmarks),
    }
    print(json.dumps(output, indent=2, ensure_ascii=False))
    conn.close()


def cmd_subscribers(args):
    """List subscribers."""
    conn = get_conn()
    subs = get_subscribers(conn)
    for s in subs:
        d = dict(s)
        print(f"  {d['name']}: {d['email']} (focus={d['focus']}, enabled={d['enabled']})")
    conn.close()


def cmd_save_summary(args):
    """Save a digest summary from stdin."""
    conn = get_conn()
    content = sys.stdin.read().strip()
    if content:
        save_summary(conn, content, period=args.period, focus=args.focus)
        print(f"Summary saved ({len(content)} chars)")
    conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Benchmark Digest — Fetch & Query")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("init", help="Initialize benchmark registry")
    sub.add_parser("fetch", help="Fetch latest scores")

    q = sub.add_parser("query", help="Query data for digest")
    q.add_argument("days", type=int, default=7, nargs="?")

    sub.add_parser("subscribers", help="List subscribers")

    sv = sub.add_parser("save-summary", help="Save summary from stdin")
    sv.add_argument("period", default="weekly", nargs="?")
    sv.add_argument("focus", default="default", nargs="?")

    args = ap.parse_args()
    if args.cmd == "init":
        cmd_init(args)
    elif args.cmd == "fetch":
        cmd_fetch(args)
    elif args.cmd == "query":
        cmd_query(args)
    elif args.cmd == "subscribers":
        cmd_subscribers(args)
    elif args.cmd == "save-summary":
        cmd_save_summary(args)
    else:
        ap.print_help()
