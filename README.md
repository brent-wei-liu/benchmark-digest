# Benchmark Digest

追踪 21+ 个 AI/ML benchmark 排行榜，通过 HuggingFace API 抓取分数、SQLite 存储，由 Hermes cron job 编排三步隔离反思流水线（Draft → Critique → Refine）生成高质量中文周报。

## 架构

```
┌─────────────────────────────────────────────────────────────┐
│  Hermes Cron Jobs                                           │
│                                                             │
│  ┌─────────────────────┐    ┌─────────────────────────────┐ │
│  │ Score Fetch (2x/wk) │    │ Weekly Digest (Sun 8pm)    │ │
│  │ Mon 10:00            │    │                             │ │
│  │ Thu 10:00            │    │  script: digest query       │ │
│  │                     │    │       ↓ JSON 注入           │ │
│  │ HuggingFace Sources │    │  Agent 编排 delegate_task   │ │
│  │ ┌─────────────────┐ │    │       ↓                     │ │
│  │ │ OpenEvals       │ │    │  ┌──────────────────────┐   │ │
│  │ │ Parquet Dataset │ │    │  │ Subagent 1: Draft    │   │ │
│  │ │ 105 models      │ │    │  │ (看得到原始数据)       │   │ │
│  │ │ 11 benchmarks   │ │    │  └──────────┬───────────┘   │ │
│  │ └─────────────────┘ │    │             ↓               │ │
│  │ ┌─────────────────┐ │    │  ┌──────────────────────┐   │ │
│  │ │ HF Leaderboard  │ │    │  │ Subagent 2: Critique │   │ │
│  │ │ API (per-bench) │ │    │  │ (只看得到初稿，隔离) │   │ │
│  │ │ SWE-bench 38    │ │    │  └──────────┬───────────┘   │ │
│  │ │ MMLU-Pro 29     │ │    │             ↓               │ │
│  │ └─────────────────┘ │    │  ┌──────────────────────┐   │ │
│  │       ↓             │    │  │ Subagent 3: Refine   │   │ │
│  │    SQLite DB        │    │  │ (初稿 + 审稿意见)    │   │ │
│  └─────────────────────┘    │  └──────────┬───────────┘   │ │
│                             │             ↓               │ │
│                             │  ┌──────────────────────┐   │ │
│                             │  │ Step 4: Save Summary │   │ │
│                             │  │ (终稿写入 SQLite DB) │   │ │
│                             │  └──────────┬───────────┘   │ │
│                             │             ↓               │ │
│                             │     最终周报 → Telegram     │ │
│                             └─────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

## 文件结构

```
~/.hermes/hermes-agent/benchmark-digest/
├── benchmark_fetch.py      # 抓取层：HuggingFace parquet + leaderboard API
├── digest_generate.py      # 摘要层：数据加载 + 三步 Prompt 模板输出
├── db.py                   # 数据层：共享 DB schema + 工具函数
├── data/
│   └── benchmarks.db       # SQLite 数据库
└── README.md

~/.hermes/scripts/
├── benchmark_fetch.py      # Cron 包装：调用 benchmark_fetch.py fetch
└── benchmark_digest.py     # Cron 包装：调用 digest_generate.py query
```

依赖：pandas + pyarrow（`pip install pandas pyarrow`）

## 追踪的内容

7 大类、21+ 个 benchmark，两个数据源。

**数据源 1：OpenEvals/leaderboard-data（聚合 Parquet，105 模型 × 11 Benchmark）**

| Benchmark | 类别 | 有数据的模型数 | 分数范围 |
|-----------|------|---------------|---------|
| AIME 2026 | reasoning | 12 | 82.5 - 96.7 |
| EvasionBench | reasoning | 5 | 66.7 - 82.9 |
| GPQA | reasoning | 30 | 11.9 - 88.4 |
| GSM8K | reasoning | 12 | 50.6 - 96.8 |
| HLE | reasoning | 31 | 4.2 - 50.2 |
| HMMT 2026 | reasoning | 12 | 53.0 - 87.9 |
| MMLU-Pro | reasoning | 35 | 29.7 - 88.0 |
| OLM-OCR | multimodal | 25 | 69.5 - 85.9 |
| SWE-bench Pro | code | 13 | 5.2 - 55.4 |
| SWE-bench Verified | code | 25 | 37.4 - 76.4 |
| TerminalBench | code | 21 | 13.0 - 52.5 |

**数据源 2：HuggingFace Dataset Leaderboard API（逐 Benchmark）**

| Benchmark | HF Dataset | 条目数 |
|-----------|-----------|--------|
| SWE-bench Verified | SWE-bench/SWE-bench_Verified | 38 |
| MMLU-Pro | TIGER-Lab/MMLU-Pro | 29 |

**7 大类别：** reasoning, multimodal, agent, code, factuality, preference, domain

当前数据库：280 个分数，13 个快照，31 个 benchmark（其中 13 个有数据），1 篇摘要。

## 核心文件说明

### benchmark_fetch.py

从 HuggingFace 抓取 benchmark 分数，存入 SQLite。需要 pandas + pyarrow。

**命令：**

| 命令 | 说明 |
|------|------|
| `init` | 初始化 benchmark 注册表（21+ benchmark 元数据） |
| `fetch` | 抓取所有数据源（parquet + API） |
| `fetch --source hf` | 只抓 OpenEvals parquet |
| `fetch --source api` | 只抓 HF leaderboard API |
| `fetch --source all` | 抓取所有数据源（默认） |
| `query [days]` | 查询 benchmark 数据，输出 JSON |
| `stats [days]` | 统计信息 |

### digest_generate.py

数据加载 + 三步 Prompt 模板输出。不调用 LLM，LLM 调用由 Hermes cron agent 通过 delegate_task 完成。

**命令：**

| 命令 | 说明 |
|------|------|
| `query [--days 7]` | 输出 benchmark 数据 + 三步 Prompt 模板 JSON |
| `save-summary [--period weekly] [--focus default]` | 从 stdin 保存摘要到 DB |
| `stats` | 简要统计 |

**query 输出 JSON 结构：**
```json
{
  "meta": { "date", "days", "total_benchmarks", "recent_scores", "categories" },
  "benchmarks": [ "活跃的 benchmark 列表" ],
  "snapshots": { "按 benchmark 名的最新排行榜快照" },
  "recent_scores": [ "最近 N 天新增的分数（最多 30 条）" ],
  "prompts": {
    "draft": "完整的初稿 Prompt（数据已嵌入）",
    "critique_template": "审稿模板（{draft} 占位符）",
    "refine_template": "精修模板（{draft} + {critique} 占位符）"
  }
}
```

### db.py

共享 DB schema + 工具函数。被 benchmark_fetch.py 和 digest_generate.py 共同引用。

**主要函数：**

| 函数 | 说明 |
|------|------|
| `get_or_create_benchmark` | 获取或创建 benchmark 记录 |
| `upsert_score` | 插入/更新模型分数 |
| `save_snapshot` | 保存排行榜快照（top models JSON） |
| `save_summary` | 保存生成的摘要 |
| `query_recent_scores` | 查询近期分数 |
| `query_benchmarks` | 查询 benchmark 列表 |
| `query_latest_snapshots` | 查询最新快照 |
| `get_subscribers` | 获取订阅者列表 |

## 三步隔离反思设计

核心思想：审稿人看不到原始数据，只能评估摘要质量。

| 步骤 | Subagent | 输入 | 输出 | 隔离 |
|------|----------|------|------|------|
| Draft | #1 | Benchmark 数据 + 排名 + 分析指令 | 初稿 | 看得到原始数据 |
| Critique | #2 | 只有初稿 | 审稿意见 + A/B/C 评分 | 看不到原始数据 |
| Refine | #3 | 初稿 + 审稿意见 | 终稿 | 看不到原始数据 |

每个 subagent 通过 Hermes `delegate_task` 创建，天然上下文隔离。

## 数据库结构

SQLite（`data/benchmarks.db`），5 张表：

| 表 | 说明 |
|----|------|
| benchmarks | 追踪的 benchmark（name, category, url, difficulty_note, is_active） |
| scores | 模型分数（benchmark_id, model, provider, score, score_unit, source_url） |
| leaderboard_snapshots | 排行榜快照（top models JSON） |
| summaries | 摘要历史（period, focus, content） |
| subscribers | 订阅者 |

## Cron Jobs

| Job | 时间 (PST) | 说明 |
|-----|-----------|------|
| Benchmark Score Fetch | 周一、四 10:00 | 抓取 HuggingFace 分数，存入 DB |
| Benchmark Weekly Digest | 周日 20:00 | 三步反思生成周报，保存到 DB，发到 Telegram |

## 手动使用

```bash
cd ~/.hermes/hermes-agent/benchmark-digest

# 初始化 benchmark 注册表
python3 benchmark_fetch.py init

# 抓取最新分数
python3 benchmark_fetch.py fetch

# 只抓 HuggingFace parquet
python3 benchmark_fetch.py fetch --source hf

# 只抓 leaderboard API
python3 benchmark_fetch.py fetch --source api

# 查看统计
python3 benchmark_fetch.py stats
python3 digest_generate.py stats

# 生成 digest JSON（不调 LLM）
python3 digest_generate.py query --days 7
```

## 迁移说明

从 OpenClaw workspace 迁移而来。主要改动：

- 数据源从 LiveBench/LMArena/GAIA 等不稳定 API 改为 HuggingFace 官方数据（原 fetcher 全部返回 0 条数据）
  - OpenEvals/leaderboard-data parquet: 105 模型 × 11 benchmark
  - HF dataset leaderboard API: SWE-bench, MMLU-Pro
- 首次抓取即获得 280 个数据点（原来 0 个）
- 新增 `digest_generate.py`（三步 Prompt 模板）
- 新增 `db.py`（共享 DB 层，从 fetch 脚本中拆出）
- LLM 调用由 Hermes delegate_task 完成
- Cron 脚本必须是 .py（Hermes scheduler 固定用 Python 解释器执行）
- Cron 脚本必须放在 `~/.hermes/scripts/`（路径校验限制）

## 已知限制

- OpenEvals parquet 数据更新频率取决于 HuggingFace 团队
- 部分 benchmark（GAIA, BrowseComp 等）在 HF 上没有公开 leaderboard API
- 旧的 benchmark 注册表条目保留但无新数据源（31 个注册，仅 13 个有数据）
- 三步 delegate_task 串行执行，生成摘要需要几分钟
