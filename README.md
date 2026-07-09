# Awesome LLM Post-Training

[![Live Site](https://img.shields.io/badge/🌐_Live_Site-GitHub_Pages-6f42c1)](https://yang-ze-kang.github.io/awesome-llm-post-training/)
[![Papers](https://img.shields.io/badge/papers-60+-blue)](data/papers.json)
[![Auto-updated](https://img.shields.io/badge/updated-daily-brightgreen)](.github/workflows/crawl.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

A curated, **bilingual (中文 / English)**, auto-updating website that categorizes
papers on Large Language Model **post-training** — supervised fine-tuning, RLHF,
preference optimization, reward modeling, RL policy optimization, reasoning /
test-time scaling, distillation, benchmarks, safety, and tooling.

<p align="center">
  <a href="https://yang-ze-kang.github.io/awesome-llm-post-training/">
    <b>🌐 Live site → yang-ze-kang.github.io/awesome-llm-post-training</b>
  </a>
</p>

> The live site deploys automatically from `main` via GitHub Pages. If the link
> 404s, the repo owner still needs to enable Pages (Settings → Pages → Source:
> **GitHub Actions**) — see [DEPLOY.md](DEPLOY.md).

Seeded from [mbzuai-oryx/Awesome-LLM-Post-training](https://github.com/mbzuai-oryx/Awesome-LLM-Post-training)
and kept fresh by a daily GitHub Action that crawls arXiv and uses Claude to
judge relevance, classify each paper, and write a bilingual one-line summary.

## Preview

```
┌────────────────────────────────────────────────────────────────────┐
│  Awesome LLM Post-Training                    [中文] [🌙] [Source]  │
│  A curated, auto-updating collection of LLM post-training papers.    │
├──────────────────────┬─────────────────────────────────────────────┤
│  🔎 Search papers... │  ▸ Supervised Fine-Tuning                    │
│                      │    Instruction Tuning              [5]        │
│  Supervised FT  [17] │    ┌───────────────────────────────────────┐ │
│    Instruction   [5] │    │ Finetuned LMs Are Zero-Shot Learners  │ │
│    PEFT          [5] │    │ Introduces instruction tuning: ...     │ │
│    Data & Synth  [4] │    │ ICLR 2022 · 2021-09-03 · Paper →       │ │
│    Distillation  [3] │    └───────────────────────────────────────┘ │
│  Reinforcement  [22] │    Parameter-Efficient FT           [5]       │
│    Reward Model  [4] │    ┌───────────────────────────────────────┐ │
│    RLHF/PPO      [4] │    │ LoRA: Low-Rank Adaptation of LLMs     │ │
│    DPO           [6] │    │ Freezes pretrained weights and ...     │ │
│    RLAIF         [3] │    └───────────────────────────────────────┘ │
│    Reasoning RL  [5] │  ▸ Reinforcement Learning                    │
│  Test-Time       [7] │    Reward Modeling (RM/PRM/ORM)     [4]       │
│  Resources      [14] │    ...                                        │
└──────────────────────┴─────────────────────────────────────────────┘
   ↑ two-level sidebar nav      ↑ papers grouped by group → category
   Toggle 中文/EN rewrites the whole UI and every summary. Search filters live.
```

## Features

- **Pure static site** — plain HTML/CSS/JS, no build step. Hosted on GitHub Pages.
- **Bilingual** — toggle the whole UI and every paper summary between 中文 and English.
- **Category navigation** — sidebar with per-category counts.
- **Live search** — filters across titles and both-language summaries.
- **Dark / light theme** — remembered across visits.
- **Daily auto-update** — arXiv + Claude crawler commits new papers to `main`.

## Project layout

```
.
├── index.html               # the page
├── assets/
│   ├── style.css            # theming + layout
│   ├── i18n.js              # UI string translations
│   └── app.js               # data loading + rendering
├── data/
│   ├── categories.json      # bilingual, hierarchical taxonomy (groups → categories)
│   └── papers.json          # the paper database (crawler appends here)
├── scripts/
│   └── crawl.py             # arXiv fetch + LLM classification
└── .github/workflows/
    ├── crawl.yml            # daily crawl → commit to main
    └── pages.yml            # deploy site to GitHub Pages on push
```

## Local preview

The page fetches JSON, so it must be served over HTTP (not opened as a `file://`):

```bash
python3 -m http.server 8000
# then open http://localhost:8000
```

## Data model

Each paper in `data/papers.json`:

```json
{
  "id": "2501.12948",
  "title": "DeepSeek-R1: Incentivizing Reasoning Capability in LLMs via RL",
  "category": "reasoning",
  "date": "2025-01-22",
  "venue": "arXiv",
  "url": "https://arxiv.org/abs/2501.12948",
  "summary": { "en": "...", "zh": "..." }
}
```

`category` must match a category `id` in `data/categories.json`, which is
organized as a two-level hierarchy — top-level **groups** (SFT, RL, Test-Time
Scaling, Resources), each containing **categories** (e.g. RL → Reward Modeling,
RLHF/PPO, DPO, RLAIF, Reasoning RL). To add a category, add an entry under the
right group (with `name`, `desc`, and `keywords` in both languages) — the UI
and crawler pick it up automatically.

### Taxonomy

- **监督微调 / Supervised Fine-Tuning** — Instruction Tuning · Parameter-Efficient FT · Data & Synthetic Data · Knowledge Distillation
- **强化学习 / Reinforcement Learning** — Reward Modeling (RM/PRM/ORM) · RLHF Policy Optimization (PPO/RLOO) · Direct Preference Optimization (DPO/IPO/KTO/SimPO/ORPO) · RLAIF & Constitutional AI · Reasoning RL / Verifiable Rewards (GRPO/R1)
- **测试时扩展 / Test-Time Scaling** — Chain-of-Thought & Reasoning · Search (MCTS / Tree)
- **资源 / Resources** — Surveys · Benchmarks & Datasets · Safety & Alignment · Tools & Frameworks

## The crawler

`scripts/crawl.py`:

1. Gathers recent candidates from **two sources**:
   - **arXiv API** — keyword + category search (`cs.CL/cs.LG/cs.AI`).
   - **Hugging Face daily papers** — community-curated, carrying **upvote counts**.
2. Merges and dedupes by arXiv id (across sources and against `papers.json`).
3. Ranks candidates **relevance-first** (keyword proxy: title hits weigh more
   than abstract hits), then by Hugging Face **upvotes**, then recency — so
   genuinely on-topic papers are processed first within the daily budget.
4. Sends each candidate's title + abstract to Claude, which returns JSON:
   `{relevant, category, summary_en, summary_zh}`. Irrelevant or
   wrong-category papers are rejected.
5. Appends accepted papers and bumps `meta.lastUpdated`.

**Failure alerting:** if there are candidates but *every one* errors out (e.g.
the LLM endpoint is down or the token is invalid), the script exits non-zero so
the GitHub Action run goes red and the repo owner is notified. The run summary
shows status, count added, and any error note.

Run it locally:

```bash
export ANTHROPIC_AUTH_TOKEN="your-key"
export ANTHROPIC_BASE_URL="https://your-endpoint"   # optional; defaults to api.anthropic.com
export ANTHROPIC_MODEL="claude-haiku-4-5"           # optional
python3 scripts/crawl.py
```

Without a token it does a **dry run**: fetches and dedupes but writes nothing.

Tunable via env vars: `CRAWL_DAYS` (lookback window, default 3),
`MAX_CANDIDATES` (papers sent to the LLM per run, default 40),
`ANTHROPIC_MODEL`, and `DISABLE_HF=1` (skip the Hugging Face source, arXiv only).

## Deployment

See [DEPLOY.md](DEPLOY.md) for one-time setup: enabling GitHub Pages and adding
the API secrets the crawler needs.

## Contributing

Add a paper by editing `data/papers.json` (keep entries sorted-agnostic; the UI
sorts by date). PRs welcome for new categories, better summaries, or fixes.

## Acknowledgements

Built on the taxonomy and paper collection of
[mbzuai-oryx/Awesome-LLM-Post-training](https://github.com/mbzuai-oryx/Awesome-LLM-Post-training).

## License

MIT
