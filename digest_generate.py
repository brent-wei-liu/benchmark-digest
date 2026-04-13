#!/usr/bin/env python3
"""
Benchmark Digest Generator — outputs benchmark data + 3-step prompt templates.

Usage:
  python3 digest_generate.py query [--days 7]
  python3 digest_generate.py save-summary [--period weekly] [--focus default]  # stdin
  python3 digest_generate.py stats
"""

import json
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent))
from db import get_conn, query_benchmarks, query_recent_scores, query_latest_snapshots


def cmd_query(args):
    days = 7
    i = 0
    while i < len(args):
        if args[i] == "--days" and i + 1 < len(args):
            days = int(args[i + 1]); i += 2
        else:
            i += 1

    conn = get_conn()
    benchmarks = [dict(b) for b in query_benchmarks(conn)]
    recent = [dict(r) for r in query_recent_scores(conn, days)]
    snapshots_raw = query_latest_snapshots(conn)

    snapshots = {}
    for s in snapshots_raw:
        snapshots[s["benchmark_name"]] = {
            "category": s["category"],
            "models": json.loads(s["top_models_json"]),
            "snapshot_at": s["snapshot_at"],
        }

    # Group benchmarks by category
    by_cat = {}
    for b in benchmarks:
        cat = b["category"]
        if cat not in by_cat:
            by_cat[cat] = []
        by_cat[cat].append(b)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Build text for prompts
    benchmark_lines = []
    for cat, bs in sorted(by_cat.items()):
        benchmark_lines.append(f"\n### {cat.upper()}")
        for b in bs:
            snap = snapshots.get(b["name"], {})
            top_models = snap.get("models", [])
            top_str = ""
            if top_models:
                top3 = top_models[:3]
                top_str = " | Top: " + ", ".join(
                    f"{m.get('model','?')}={m.get('score','?')}" for m in top3
                )
            benchmark_lines.append(
                f"- **{b['name']}** ({b.get('difficulty_note','')}) [{b.get('url','')}]{top_str}"
            )

    recent_lines = []
    for r in recent[:30]:
        recent_lines.append(
            f"- {r['benchmark_name']} ({r['category']}): {r['model']} = {r['score']}{r.get('score_unit','%')} [{r['fetched_at']}]"
        )

    benchmarks_text = "\n".join(benchmark_lines)
    recent_text = "\n".join(recent_lines) if recent_lines else "无新数据"

    draft_prompt = f"""你是 AI Benchmark 中文周报的撰稿人。请根据以下数据撰写一份精炼的中文分析报告。

日期：{today}
追踪的 Benchmark 数量：{len(benchmarks)}
最近 {days} 天新增数据点：{len(recent)}

## 追踪的 Benchmarks

{benchmarks_text}

## 最近 {days} 天新增分数

{recent_text}

## 要求

1. 用中文撰写，Benchmark 名称和模型名保留英文
2. 按类别分析（Reasoning / Multimodal / Agent / Code / Factuality / Preference）
3. 重点分析：
   - 哪些模型在哪些 benchmark 上取得了突破？
   - 不同厂商（OpenAI / Anthropic / Google / Meta）的竞争格局
   - 哪些 benchmark 已经饱和？哪些仍有很大提升空间？
4. 给出 "本周观察"（2-3 句话总结趋势）
5. 总长控制在 800-1200 字"""

    critique_template = """你是一位 AI 评测领域的资深研究员。请审阅以下 Benchmark 周报初稿。

## 初稿

{draft}

## 审稿要求

1. 数据引用是否准确？分数对比是否有误？
2. 竞争格局分析是否客观？有没有偏向某家厂商？
3. "饱和" 判断是否合理？
4. 有没有遗漏重要的排名变化？
5. 趋势分析是否有深度？

请按 A/B/C 评级并给出具体修改建议。"""

    refine_template = """你是 AI Benchmark 周报的终稿编辑。请根据审稿意见修改初稿。

## 初稿

{draft}

## 审稿意见

{critique}

## 要求

1. 根据审稿意见逐条修改
2. 确保数据引用准确
3. 终稿直接输出，不要包含修改说明"""

    output = {
        "meta": {
            "date": today,
            "days": days,
            "total_benchmarks": len(benchmarks),
            "recent_scores": len(recent),
            "categories": list(by_cat.keys()),
        },
        "benchmarks": benchmarks,
        "snapshots": snapshots,
        "recent_scores": recent[:30],
        "prompts": {
            "draft": draft_prompt,
            "critique_template": critique_template,
            "refine_template": refine_template,
        },
    }

    print(json.dumps(output, ensure_ascii=False, indent=2))
    conn.close()


def cmd_save_summary(args):
    content = sys.stdin.read().strip()
    if not content:
        print('{"error": "no content on stdin"}')
        return
    period = "weekly"
    focus = "default"
    i = 0
    while i < len(args):
        if args[i] == "--period" and i + 1 < len(args):
            period = args[i + 1]; i += 2
        elif args[i] == "--focus" and i + 1 < len(args):
            focus = args[i + 1]; i += 2
        else:
            i += 1

    conn = get_conn()
    from db import save_summary
    save_summary(conn, content, period=period, focus=focus)
    print(json.dumps({"saved": True, "period": period, "focus": focus, "chars": len(content)}))
    conn.close()


def cmd_stats():
    conn = get_conn()
    benchmarks = conn.execute("SELECT COUNT(*) FROM benchmarks").fetchone()[0]
    scores = conn.execute("SELECT COUNT(*) FROM scores").fetchone()[0]
    snapshots = conn.execute("SELECT COUNT(*) FROM leaderboard_snapshots").fetchone()[0]
    summaries = conn.execute("SELECT COUNT(*) FROM summaries").fetchone()[0]
    print(json.dumps({
        "benchmarks": benchmarks,
        "scores": scores,
        "snapshots": snapshots,
        "summaries": summaries,
    }, indent=2))
    conn.close()


def main():
    if len(sys.argv) < 2 or sys.argv[1] == "query":
        cmd_query(sys.argv[2:] if len(sys.argv) > 2 else [])
    elif sys.argv[1] == "save-summary":
        cmd_save_summary(sys.argv[2:])
    elif sys.argv[1] == "stats":
        cmd_stats()
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
