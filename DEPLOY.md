# Deployment guide

One-time setup to get the site live and the daily crawler running.

## 1. Push to GitHub

```bash
cd awesome-llm-post-training
git init
git add .
git commit -m "Initial commit: Awesome LLM Post-Training site + crawler"
git branch -M main
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```

## 2. Enable GitHub Pages

1. Repo → **Settings** → **Pages**.
2. Under **Build and deployment**, set **Source** to **GitHub Actions**.

The `pages.yml` workflow deploys on every push to `main`. After the first run,
your site is at `https://<you>.github.io/<repo>/`.

## 3. Add crawler secrets

The daily crawler calls Claude. Add these under
**Settings → Secrets and variables → Actions**:

**Secrets** (encrypted):

| Name                   | Value                                                        |
| ---------------------- | ------------------------------------------------------------ |
| `ANTHROPIC_AUTH_TOKEN` | Your API key / token.                                        |
| `ANTHROPIC_BASE_URL`   | Your endpoint, e.g. `https://api.anthropic.com` or a proxy.  |

**Variables** (optional, not secret):

| Name              | Value                              |
| ----------------- | ---------------------------------- |
| `ANTHROPIC_MODEL` | e.g. `claude-haiku-4-5` (default). |

> The crawler sends requests to `${ANTHROPIC_BASE_URL}/v1/messages` using the
> standard Anthropic Messages API with an `x-api-key` header. Any
> Anthropic-compatible endpoint works.

## 4. Test the crawler manually

Repo → **Actions** → **Daily paper crawl** → **Run workflow**. Check the run log;
if new papers are found it commits to `main`, which triggers a Pages redeploy.

## Schedule

- **Crawl**: daily at 01:17 UTC (`.github/workflows/crawl.yml`). Adjust the
  `cron` there if you want a different time.
- **Deploy**: on every push to `main` (`.github/workflows/pages.yml`).

## Cost note

The crawler sends up to `MAX_CANDIDATES` (default 40) title+abstract pairs to the
LLM per day, each a short single-turn request. Using a small model like
`claude-haiku-4-5` keeps this cheap. Lower `MAX_CANDIDATES` or widen the schedule
to reduce spend further.

## Troubleshooting

- **Pages 404**: confirm Settings → Pages source is **GitHub Actions**, and the
  `pages.yml` run succeeded.
- **Crawler writes nothing**: no token set (dry run), or no new relevant papers.
  Check the run log — it prints each decision.
- **Data won't load locally**: you opened `index.html` as a file. Serve over HTTP
  (`python3 -m http.server`).
