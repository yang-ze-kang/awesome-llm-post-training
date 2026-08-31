# Deployment guide

One-time setup for the GitHub Pages site, daily GPT-5.6 crawler, and crawl-usage dashboard.

## 1. Push the repository

```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```

If this is a fork, the remote is already configured.

## 2. Enable GitHub Pages

1. Open **Settings → Pages** in the repository.
2. Under **Build and deployment**, set **Source** to **GitHub Actions**.
3. Run **Deploy to GitHub Pages** once from the Actions tab, or push to `main`.

The site will be available at `https://<you>.github.io/<repo>/`.

## 3. Configure the crawler

Open **Settings → Secrets and variables → Actions**.

### Required secret

| Name | Value |
| --- | --- |
| `OPENAI_API_KEY` | An OpenAI API key with access to the selected model. |

### Optional variables

| Name | Default | Notes |
| --- | --- | --- |
| `OPENAI_MODEL` | `gpt-5.6-terra` | Use `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`, or another supported model. |
| `OPENAI_REASONING_EFFORT` | `low` | One of `none`, `low`, `medium`, `high`, `xhigh`, `max`. |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | Only needed for an OpenAI-compatible Responses API endpoint. |

Undefined GitHub variables are passed as empty strings; the crawler treats them as unset and keeps its defaults.

The implementation uses `POST /v1/responses`, bearer authentication, and structured JSON output. The official [GPT-5.6 Terra documentation](https://developers.openai.com/api/docs/models/gpt-5.6-terra) lists the model ID, endpoint support, token prices, and reasoning levels.

## 4. Test manually

1. Open **Actions → Daily paper crawl → Run workflow**.
2. Inspect the job summary. It reports model, evaluated and added papers, token totals, errors, and estimated API cost.
3. Confirm that the bot commit updates `data/crawl-stats.json` and, when papers are accepted, `data/papers.json`.
4. Wait for the chained Pages deployment, then open **Crawl stats / 爬取统计** on the site.

Statistics start with the first run of this version. Empty, dry-run, and failed model executions are also recorded when the crawler reaches its finalization step.

## Schedule and data flow

- **Crawl:** daily at `01:17 UTC`, configured in `.github/workflows/crawl.yml`.
- **Deploy:** on direct pushes to `main`, manual dispatches, and successful completion of the daily crawl.
- **History:** every measured run appends one record to `data/crawl-stats.json`.

The deployment workflow listens to `workflow_run` because commits pushed by a workflow using the built-in `GITHUB_TOKEN` do not trigger another `push` workflow. This explicit chain ensures the latest papers and statistics reach Pages without a Personal Access Token.

## Cost accounting

The crawler sums usage from every successful Responses API call, including calls whose output is later rejected or unparsable. It tracks:

- input, cached-input, and cache-write tokens;
- output and reasoning tokens;
- total tokens and API calls;
- estimated token cost using the model rates stored with that run.

The estimate includes GPT-5.6 cache-write and long-context multipliers. It does not claim to replace the OpenAI billing dashboard: alternative service tiers, non-token tools, or future pricing can differ.

Reduce `MAX_CANDIDATES` in `.github/workflows/crawl.yml`, lower `CRAWL_DAYS`, or select a lower-cost GPT-5.6 tier to control spend.

## Troubleshooting

- **Pages returns 404:** confirm that Pages uses **GitHub Actions** and that the deploy job succeeded.
- **Crawler is a dry run:** `OPENAI_API_KEY` is missing or unavailable to the workflow (forked pull requests cannot read repository secrets).
- **Every candidate errors:** verify model access, key validity, and `OPENAI_BASE_URL`; the crawl job is deliberately marked failed after recording the run.
- **No papers are added:** this is normal when candidates are duplicates or judged out of scope. The run statistics should still update.
- **Statistics button is empty:** run the updated crawler at least once and verify that `data/crawl-stats.json` was committed and Pages redeployed.
- **Local page cannot load JSON:** serve the repository with `python3 -m http.server`; do not open `index.html` via `file://`.
