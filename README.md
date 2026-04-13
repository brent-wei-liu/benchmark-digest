# Benchmark Digest

追踪 21+ 个 AI/ML benchmark 排行榜，通过 HuggingFace API 抓取分数 → SQLite 存储 → 三步隔离 LLM 反思生成中文周报。

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
├── digest_generate.py      # 摘要层：数据加载 + 三步 Prompt 模板
├── db.py                   # 数据层：共享 DB schema + 工具函数
├── data/
│   └── benchmarks.db       # SQLite 数据库
└── README.md

~/.hermes/scripts/
├── benchmark_fetch.py      # Cron 包装：调用 benchmark_fetch.py fetch
└── benchmark_digest.py     # Cron 包装：调用 digest_generate.py query
```

## 依赖

- Python 3.9+
- pandas + pyarrow（`pip install pandas pyarrow`）

## 数据源

### 1. OpenEvals/leaderboard-data（聚合 Parquet）

HuggingFace 官方聚合数据集，一个 Parquet 文件包含 105 个模型 × 11 个 benchmark 的分数。

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

### 2. HuggingFace Dataset Leaderboard API（逐 Benchmark）

直接调 `https://huggingface.co/api/datasets/{id}/leaderboard`，获取最新排名。

| Benchmark | HF Dataset | 条目数 |
|-----------|-----------|--------|
| SWE-bench Verified | SWE-bench/SWE-bench_Verified | 38 |
| MMLU-Pro | TIGER-Lab/MMLU-Pro | 29 |

## 核心文件说明

### benchmark_fetch.py

从 HuggingFace 抓取 benchmark 分数，存入 SQLite。

| 命令 | 说明 |
|------|------|
| `init` | 初始化 benchmark 注册表 |
| `fetch` | 抓取所有数据源 |
| `fetch --source hf` | 只抓 OpenEvals parquet |
| `fetch --source api` | 只抓 HF leaderboard API |
| `query [days]` | 查询 benchmark 数据，输出 JSON |
| `stats [days]` | 统计信息 |

### digest_generate.py

数据加载 + 三步 Prompt 模板输出。不调用 LLM。

| 命令 | 说明 |
|------|------|
| `query [--days 7]` | 输出 benchmark 数据 + 三步 Prompt 模板 JSON |
| `save-summary [--period weekly] [--focus default]` | 从 stdin 保存摘要到 DB |
| `stats` | 简要统计 |

### db.py

共享 DB schema + 工具函数（upsert_score, save_snapshot, query 等）。

## 三步隔离反思设计

| 步骤 | Subagent | 输入 | 输出 |
|------|----------|------|------|
| Draft | #1 | Benchmark 数据 + 排名 + 分析指令 | 初稿 |
| Critique | #2 | 只有初稿 | 审稿意见 + A/B/C 评分 |
| Refine | #3 | 初稿 + 审稿意见 | 终稿 |

## 数据库结构（5 张表）

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
| Benchmark Score Fetch | 周一、四 10:00 | 抓取 HuggingFace 分数 |
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

# 查看统计
python3 benchmark_fetch.py stats
python3 digest_generate.py stats

# 生成 digest JSON（不调 LLM）
python3 digest_generate.py query --days 7
```

## 迁移说明

从 OpenClaw workspace 迁移而来。主要改动：

- 数据源从 LiveBench/LMArena 等不稳定 API 改为 HuggingFace 官方数据
  - OpenEvals/leaderboard-data parquet: 105 模型 × 11 benchmark
  - HF dataset leaderboard API: SWE-bench, MMLU-Pro
- 首次抓取即获得 280 个数据点（原来 0 个）
- 新增 `digest_generate.py`（三步 Prompt 模板）
- LLM 调用由 Hermes delegate_task 完成

## 已知限制

- OpenEvals parquet 数据更新频率取决于 HuggingFace 团队
- 部分 benchmark（GAIA, BrowseComp 等）在 HF 上没有公开 leaderboard API
- 旧的 benchmark 注册表条目保留但无新数据源

## License

MIT
