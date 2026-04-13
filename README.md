# Benchmark Digest

追踪 21 个 AI/ML benchmark 排行榜，抓取最新分数 → SQLite 存储 → 三步隔离 LLM 反思生成中文周报。

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
│  │ LiveBench API       │    │  Agent 编排 delegate_task   │ │
│  │ LMArena API         │    │       ↓                     │ │
│  │ GAIA API            │    │  Draft → Critique → Refine  │ │
│  │       ↓             │    │       ↓                     │ │
│  │    SQLite DB        │    │  Save Summary → Telegram    │ │
│  └─────────────────────┘    └─────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

## 文件结构

```
~/.hermes/hermes-agent/benchmark-digest/
├── benchmark_fetch.py      # 抓取层：多源 API 抓取 + benchmark 注册
├── digest_generate.py      # 摘要层：数据加载 + 三步 Prompt 模板
├── db.py                   # 数据层：共享 DB schema + 工具函数
├── data/
│   └── benchmarks.db       # SQLite 数据库
└── README.md

~/.hermes/scripts/
├── benchmark_fetch.py      # Cron 包装：调用 benchmark_fetch.py fetch
└── benchmark_digest.py     # Cron 包装：调用 digest_generate.py query
```

## 追踪的 21 个 Benchmarks

| 类别 | Benchmark | 难度说明 |
|------|-----------|----------|
| **Reasoning** | LiveBench, ARC-AGI-2, GPQA-Diamond, HLE | Top models <70%, LLMs score 0% |
| **Multimodal** | MMMU, MMMU-Pro, MathVista, Video-MME, EgoSchema, ChartQA | 大学/视频/图表理解 |
| **Agent** | GAIA, BrowseComp, Tau-bench, MM-BrowseComp | 通用 Agent ~50% |
| **Code** | SWE-bench Verified, SWE-bench Pro, SciCode | ~70% top, 科学代码 |
| **Factuality** | SimpleQA, HHEM | 事实召回/幻觉量化 |
| **Preference** | LMArena | 人类盲测 + 风格控制 |
| **Domain** | Scale SEAL | 法律/金融专业 |

## 数据源

| 数据源 | API | 状态 |
|--------|-----|------|
| LiveBench | `livebench.ai/api/leaderboard` | JSON API |
| LMArena | `lmarena.ai/api/v1/leaderboard` | JSON API |
| GAIA | HuggingFace Spaces | 待实现 |

## 数据库结构（5 张表）

| 表 | 说明 |
|----|------|
| benchmarks | 21 个追踪的 benchmark（name, category, url, difficulty_note） |
| scores | 模型分数（benchmark_id, model, provider, score, score_unit） |
| leaderboard_snapshots | 排行榜快照（top models JSON） |
| summaries | 摘要历史 |
| subscribers | 订阅者 |

## Cron Jobs

| Job | 时间 (PST) | 说明 |
|-----|-----------|------|
| Benchmark Score Fetch | 周一、四 10:00 | 抓取 API 分数 |
| Benchmark Weekly Digest | 周日 20:00 | 三步反思生成周报，保存到 DB，发到 Telegram |

## 手动使用

```bash
cd ~/.hermes/hermes-agent/benchmark-digest

# 初始化 benchmark 注册表
python3 benchmark_fetch.py init

# 抓取最新分数
python3 benchmark_fetch.py fetch

# 查看统计
python3 digest_generate.py stats

# 生成 digest JSON
python3 digest_generate.py query --days 7
```

## 迁移说明

从 OpenClaw workspace 迁移而来。主要改动：
- 新增 `digest_generate.py`（原来没有独立的 digest 生成脚本）
- LLM 调用由 Hermes delegate_task 完成
- 新增三步反思架构

## License

MIT
