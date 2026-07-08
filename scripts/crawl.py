#!/usr/bin/env python3
"""Daily arXiv crawler with LLM-assisted classification.

Pipeline:
  1. Query arXiv for recent papers matching post-training / reasoning / RLHF topics.
  2. Drop anything already present in data/papers.json (dedupe by arXiv id).
  3. Ask Claude to judge relevance, pick a category, and write a bilingual
     (EN/ZH) one-line summary for each new candidate.
  4. Append accepted papers to data/papers.json and bump meta.lastUpdated.

Environment variables:
  ANTHROPIC_AUTH_TOKEN  (required for LLM step) API key / token
  ANTHROPIC_BASE_URL    (optional) third-party Anthropic-compatible endpoint
  ANTHROPIC_MODEL       (optional) defaults to claude-haiku-4-5
  MAX_CANDIDATES        (optional) cap papers sent to the LLM per run (default 40)
  CRAWL_DAYS            (optional) how many days back to search (default 3)

Without a token the script still runs: it fetches and dedupes, but skips the
LLM step and writes nothing (keyword pre-filtering only, for a dry run).
"""

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
PAPERS_FILE = DATA_DIR / "papers.json"
CATEGORIES_FILE = DATA_DIR / "categories.json"

ARXIV_API = "http://export.arxiv.org/api/query"

# arXiv search: relevant categories + topical keywords.
SEARCH_QUERY = (
    '(cat:cs.CL OR cat:cs.LG OR cat:cs.AI) AND '
    '(abs:"post-training" OR abs:"RLHF" OR abs:"reinforcement learning from human feedback" '
    'OR abs:"preference optimization" OR abs:"reward model" OR abs:"chain-of-thought" '
    'OR abs:"reasoning" OR abs:"instruction tuning" OR abs:"DPO" OR abs:"GRPO" '
    'OR abs:"test-time scaling" OR abs:"process reward")'
)

MAX_CANDIDATES = int(os.environ.get("MAX_CANDIDATES", "40"))
CRAWL_DAYS = int(os.environ.get("CRAWL_DAYS", "3"))
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5")


def log(msg):
    print(f"[crawl] {msg}", flush=True)


# ----------------------------------------------------------------------------
# arXiv fetching
# ----------------------------------------------------------------------------
def fetch_arxiv(max_results=100):
    params = {
        "search_query": SEARCH_QUERY,
        "start": 0,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    url = ARXIV_API + "?" + urllib.parse.urlencode(params)
    log(f"Querying arXiv: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "awesome-llm-post-training-crawler/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read().decode("utf-8")
    return parse_arxiv(raw)


def parse_arxiv(xml_text):
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(xml_text)
    cutoff = datetime.now(timezone.utc) - timedelta(days=CRAWL_DAYS)
    out = []
    for entry in root.findall("atom:entry", ns):
        arxiv_url = entry.findtext("atom:id", default="", namespaces=ns)
        m = re.search(r"arxiv\.org/abs/([0-9]+\.[0-9]+)", arxiv_url)
        if not m:
            continue
        arxiv_id = m.group(1)
        published = entry.findtext("atom:published", default="", namespaces=ns)
        try:
            pub_dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
        except ValueError:
            pub_dt = None
        if pub_dt and pub_dt < cutoff:
            continue
        title = " ".join((entry.findtext("atom:title", default="", namespaces=ns)).split())
        summary = " ".join((entry.findtext("atom:summary", default="", namespaces=ns)).split())
        out.append({
            "id": arxiv_id,
            "title": title,
            "abstract": summary,
            "date": pub_dt.strftime("%Y-%m-%d") if pub_dt else "",
            "url": f"https://arxiv.org/abs/{arxiv_id}",
        })
    log(f"Fetched {len(out)} candidates within last {CRAWL_DAYS} day(s).")
    return out


# ----------------------------------------------------------------------------
# LLM classification
# ----------------------------------------------------------------------------
def build_prompt(candidate, categories):
    cat_lines = "\n".join(
        f"- {c['id']}: {c['name']['en']} — {c['desc']['en']}" for c in categories
    )
    return f"""You are curating an "Awesome LLM Post-Training" paper list. Post-training \
covers what happens AFTER pretraining: supervised fine-tuning, RLHF, preference \
optimization, reward modeling, RL policy optimization, reasoning / test-time scaling, \
distillation, related benchmarks, safety alignment, and tooling.

Available categories:
{cat_lines}

Evaluate this paper:
Title: {candidate['title']}
Abstract: {candidate['abstract']}

Respond with ONLY a JSON object (no markdown fences), with keys:
- "relevant": boolean — true only if this paper genuinely belongs in an LLM \
post-training list.
- "category": one of the category ids above (or null if not relevant).
- "summary_en": a single concise sentence (<= 30 words) describing the contribution.
- "summary_zh": the same summary in Simplified Chinese.

If not relevant, set relevant=false and the other fields may be null."""


def call_claude(prompt, token, base_url):
    endpoint = base_url.rstrip("/") + "/v1/messages"
    body = json.dumps({
        "model": MODEL,
        "max_tokens": 400,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=body,
        headers={
            "content-type": "application/json",
            "x-api-key": token,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    parts = payload.get("content", [])
    text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
    return text.strip()


def parse_llm_json(text):
    # Strip accidental markdown fences.
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def classify(candidate, categories, token, base_url, valid_ids):
    prompt = build_prompt(candidate, categories)
    for attempt in range(3):
        try:
            raw = call_claude(prompt, token, base_url)
            data = parse_llm_json(raw)
            if not data:
                log(f"  {candidate['id']}: unparseable LLM response, skipping.")
                return None
            if not data.get("relevant"):
                return None
            cat = data.get("category")
            if cat not in valid_ids:
                log(f"  {candidate['id']}: invalid category '{cat}', skipping.")
                return None
            return {
                "id": candidate["id"],
                "title": candidate["title"],
                "category": cat,
                "date": candidate["date"],
                "venue": "arXiv",
                "url": candidate["url"],
                "summary": {
                    "en": (data.get("summary_en") or "").strip(),
                    "zh": (data.get("summary_zh") or "").strip(),
                },
            }
        except Exception as e:  # noqa: BLE001 - network/transient, retry
            wait = 2 ** attempt
            log(f"  {candidate['id']}: LLM error ({e}); retry in {wait}s.")
            time.sleep(wait)
    return None


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------
def main():
    categories = json.loads(CATEGORIES_FILE.read_text())["categories"]
    valid_ids = {c["id"] for c in categories}
    db = json.loads(PAPERS_FILE.read_text())
    existing_ids = {p["id"] for p in db["papers"]}

    candidates = fetch_arxiv()
    new_candidates = [c for c in candidates if c["id"] not in existing_ids]
    log(f"{len(new_candidates)} candidate(s) not already in the list.")
    new_candidates = new_candidates[:MAX_CANDIDATES]

    token = os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")
    base_url = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com").strip()

    if not token:
        log("No ANTHROPIC_AUTH_TOKEN set; skipping LLM step (dry run). Nothing written.")
        log(f"Would have evaluated {len(new_candidates)} candidate(s).")
        return 0

    accepted = []
    for i, cand in enumerate(new_candidates, 1):
        log(f"[{i}/{len(new_candidates)}] {cand['title'][:70]}...")
        result = classify(cand, categories, token, base_url, valid_ids)
        if result:
            accepted.append(result)
            log(f"  ✓ accepted -> {result['category']}")
        time.sleep(1)  # gentle pacing

    if not accepted:
        log("No new relevant papers accepted. Data unchanged.")
        return 0

    db["papers"].extend(accepted)
    db["meta"]["lastUpdated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    PAPERS_FILE.write_text(json.dumps(db, ensure_ascii=False, indent=2) + "\n")
    log(f"Added {len(accepted)} paper(s). Total now {len(db['papers'])}.")

    # Emit a summary for the GitHub Action commit message / step output.
    titles = "; ".join(p["title"] for p in accepted)
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a") as fh:
            fh.write(f"added_count={len(accepted)}\n")
            fh.write(f"added_titles={titles}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
