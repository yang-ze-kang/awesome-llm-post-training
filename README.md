# Awesome LLM Post-Training

A curated, **bilingual (中文 / English)**, auto-updating website that categorizes
papers on Large Language Model **post-training** — supervised fine-tuning, RLHF,
preference optimization, reward modeling, RL policy optimization, reasoning /
test-time scaling, distillation, benchmarks, safety, and tooling.

Seeded from [mbzuai-oryx/Awesome-LLM-Post-training](https://github.com/mbzuai-oryx/Awesome-LLM-Post-training)
and kept fresh by a daily GitHub Action that crawls arXiv and uses Claude to
judge relevance, classify each paper, and write a bilingual one-line summary.

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
│   ├── categories.json      # bilingual category taxonomy
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

`category` must match an `id` in `data/categories.json`. To add a category, add
an entry there (with `name`, `desc`, and `keywords` in both languages) — the UI
and crawler pick it up automatically.

## The crawler

`scripts/crawl.py`:

1. Queries the arXiv API for recent papers (`cs.CL/cs.LG/cs.AI`) matching
   post-training / reasoning keywords.
2. Drops any arXiv id already in `papers.json`.
3. Sends each new candidate's title + abstract to Claude, which returns JSON:
   `{relevant, category, summary_en, summary_zh}`. Irrelevant or
   wrong-category papers are rejected.
4. Appends accepted papers and bumps `meta.lastUpdated`.

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
`ANTHROPIC_MODEL`.

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
