#!/usr/bin/env python3
"""Daily paper crawler with LLM-assisted classification.

Pipeline:
  1. Gather recent candidate papers from multiple sources:
       - arXiv API (keyword + category search)
       - Hugging Face daily papers (community-curated, carries upvote counts)
  2. Merge + dedupe by arXiv id, and against papers already in data/papers.json.
  3. Rank candidates by topical relevance first (keyword proxy), then by
     popularity (Hugging Face upvotes) and recency, so genuinely on-topic
     papers are processed first within the per-run budget.
  4. Ask Claude to judge relevance, pick a category, and write a bilingual
     (EN/ZH) one-line summary for each new candidate.
  5. Append accepted papers to data/papers.json and bump meta.lastUpdated.

Environment variables:
  ANTHROPIC_AUTH_TOKEN  (required for LLM step) API key / token
  ANTHROPIC_BASE_URL    (optional) third-party Anthropic-compatible endpoint
  ANTHROPIC_MODEL       (optional) defaults to claude-haiku-4-5-20251001
  MAX_CANDIDATES        (optional) cap papers sent to the LLM per run (default 40)
  CRAWL_DAYS            (optional) how many days back to search (default 3)
  DISABLE_HF            (optional) set to "1" to skip the Hugging Face source

Exit codes:
  0  success (papers added, or nothing new / relevant)
  2  every candidate failed due to errors (likely LLM endpoint down) — this
     marks the GitHub Action run red so the owner gets a failure notification.

Without a token the script still runs: it fetches, dedupes, and ranks, but
skips the LLM step and writes nothing (a dry run).
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
HF_API = "https://huggingface.co/api/daily_papers"

# arXiv search: relevant categories + topical keywords. Kept broad on purpose —
# the LLM does the precise relevance filtering downstream, so recall matters
# more than precision here.
SEARCH_QUERY = (
    '(cat:cs.CL OR cat:cs.LG OR cat:cs.AI) AND '
    '(abs:"post-training" OR abs:"post training" OR abs:"RLHF" '
    'OR abs:"reinforcement learning from human feedback" OR abs:"RLAIF" '
    'OR abs:"preference optimization" OR abs:"DPO" OR abs:"reward model" '
    'OR abs:"reward modeling" OR abs:"process reward" OR abs:"chain-of-thought" '
    'OR abs:"chain of thought" OR abs:"reasoning" OR abs:"instruction tuning" '
    'OR abs:"instruction-following" OR abs:"supervised fine-tuning" '
    'OR abs:"fine-tuning" OR abs:"GRPO" OR abs:"PPO" OR abs:"RLVR" '
    'OR abs:"verifiable reward" OR abs:"test-time scaling" '
    'OR abs:"inference-time" OR abs:"self-improvement" OR abs:"alignment" '
    'OR abs:"LoRA" OR abs:"distillation" OR abs:"self-training")'
)

# Keywords used to pre-filter the Hugging Face feed (which is not topic-scoped)
# before spending LLM calls. Broad by design; the LLM decides final relevance.
HF_KEYWORDS = [
    "post-training", "post training", "rlhf", "rlaif", "preference",
    "dpo", "reward model", "process reward", "chain-of-thought",
    "chain of thought", "reasoning", "instruction tuning", "instruction-following",
    "fine-tuning", "finetuning", "supervised fine-tuning", "grpo", "ppo",
    "rlvr", "verifiable reward", "test-time", "inference-time", "alignment",
    "lora", "distillation", "self-training", "self-improvement", "self-reward",
]

MAX_CANDIDATES = int(os.environ.get("MAX_CANDIDATES", "40"))
CRAWL_DAYS = int(os.environ.get("CRAWL_DAYS", "3"))
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
DISABLE_HF = os.environ.get("DISABLE_HF", "") == "1"


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
    log(f"Querying arXiv (last {CRAWL_DAYS}d)...")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "awesome-llm-post-training-crawler/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
    except Exception as e:  # noqa: BLE001
        log(f"arXiv fetch failed ({e}); continuing with other sources.")
        return []
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
            "upvotes": 0,
            "source": "arxiv",
        })
    log(f"arXiv: {len(out)} candidate(s) within window.")
    return out


# ----------------------------------------------------------------------------
# Hugging Face daily papers
# ----------------------------------------------------------------------------
def fetch_huggingface():
    """Fetch community-curated papers from Hugging Face daily papers, which
    carry upvote counts we use for popularity ranking. Filtered to on-topic
    entries by keyword before the LLM step."""
    if DISABLE_HF:
        log("Hugging Face source disabled (DISABLE_HF=1).")
        return []
    out = []
    seen = set()
    today = datetime.now(timezone.utc).date()
    for day_offset in range(CRAWL_DAYS):
        date_str = (today - timedelta(days=day_offset)).strftime("%Y-%m-%d")
        url = f"{HF_API}?date={date_str}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "awesome-llm-post-training-crawler/1.0"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                items = json.loads(resp.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            log(f"Hugging Face fetch failed for {date_str} ({e}); skipping that day.")
            continue
        for item in items or []:
            paper = item.get("paper", item) or {}
            arxiv_id = paper.get("id") or ""
            if not re.match(r"^[0-9]+\.[0-9]+$", arxiv_id) or arxiv_id in seen:
                continue
            title = " ".join((paper.get("title") or "").split())
            abstract = " ".join((paper.get("summary") or "").split())
            haystack = (title + " " + abstract).lower()
            if not any(kw in haystack for kw in HF_KEYWORDS):
                continue
            seen.add(arxiv_id)
            published = item.get("publishedAt") or paper.get("publishedAt") or ""
            date = published[:10] if len(published) >= 10 else date_str
            out.append({
                "id": arxiv_id,
                "title": title,
                "abstract": abstract,
                "date": date,
                "url": f"https://arxiv.org/abs/{arxiv_id}",
                "upvotes": int(paper.get("upvotes") or 0),
                "source": "huggingface",
            })
    log(f"Hugging Face: {len(out)} on-topic candidate(s) within window.")
    return out


# ----------------------------------------------------------------------------
# merge + rank
# ----------------------------------------------------------------------------
def merge_candidates(*sources):
    """Merge candidate lists by arXiv id, keeping the richest record and the
    highest upvote count seen for each paper."""
    merged = {}
    for src in sources:
        for c in src:
            cur = merged.get(c["id"])
            if cur is None:
                merged[c["id"]] = dict(c)
            else:
                cur["upvotes"] = max(cur.get("upvotes", 0), c.get("upvotes", 0))
                # Prefer a longer abstract if one source had a fuller record.
                if len(c.get("abstract", "")) > len(cur.get("abstract", "")):
                    cur["abstract"] = c["abstract"]
                if c.get("source") == "huggingface":
                    cur["source"] = cur.get("source", "") + "+hf"
    return list(merged.values())


def relevance_score(candidate):
    """Cheap keyword relevance proxy used to order candidates before the LLM
    step. A title hit weighs more than an abstract hit, since titles are
    denser signal. This is only for ordering — the LLM makes the final call."""
    title = (candidate.get("title") or "").lower()
    abstract = (candidate.get("abstract") or "").lower()
    score = 0
    for kw in HF_KEYWORDS:
        if kw in title:
            score += 3
        elif kw in abstract:
            score += 1
    return score


def rank_candidates(candidates):
    """Order candidates for the LLM budget: strongest topical relevance first
    (keyword proxy), then community popularity (HF upvotes), then recency.

    Relevance leads so genuinely on-topic papers are processed before viral
    but off-topic ones; popularity and date only break ties."""
    return sorted(
        candidates,
        key=lambda c: (relevance_score(c), c.get("upvotes", 0), c.get("date", "")),
        reverse=True,
    )


# ----------------------------------------------------------------------------
# LLM classification
# ----------------------------------------------------------------------------
def build_prompt(candidate, categories):
    cat_lines = "\n".join(
        f"- {c['id']}: [{c['group']}] {c['name']['en']} — {c['desc']['en']}"
        for c in categories
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


class LLMError(Exception):
    """Raised when a candidate could not be evaluated due to an error (network,
    endpoint, or unparseable response) as opposed to being judged irrelevant."""


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
            # Some Anthropic-compatible proxies sit behind Cloudflare, which
            # blocks the default Python-urllib User-Agent (error 1010). Send a
            # conventional UA so the request is allowed through.
            "User-Agent": "awesome-llm-post-training-crawler/1.0",
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
    """Return an accepted paper dict, or None if judged irrelevant.

    Raises LLMError if the candidate could not be evaluated after retries, so
    callers can distinguish 'rejected' from 'endpoint is down'."""
    prompt = build_prompt(candidate, categories)
    last_err = None
    for attempt in range(3):
        try:
            raw = call_claude(prompt, token, base_url)
        except Exception as e:  # noqa: BLE001 - network/transient, retry
            last_err = e
            wait = 2 ** attempt
            log(f"  {candidate['id']}: LLM error ({e}); retry in {wait}s.")
            time.sleep(wait)
            continue
        data = parse_llm_json(raw)
        if not data:
            log(f"  {candidate['id']}: unparseable LLM response, skipping.")
            raise LLMError("unparseable response")
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
    raise LLMError(str(last_err))


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------
def load_categories():
    """Flatten the groups -> categories hierarchy into a list of leaf categories,
    tagging each with its parent group name for a richer LLM prompt."""
    raw = json.loads(CATEGORIES_FILE.read_text())
    cats = []
    for group in raw.get("groups", []):
        for cat in group.get("categories", []):
            cat = dict(cat)
            cat["group"] = group["name"]["en"]
            cats.append(cat)
    return cats


def emit_gh_output(**kwargs):
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if not gh_out:
        return
    with open(gh_out, "a") as fh:
        for k, v in kwargs.items():
            fh.write(f"{k}={v}\n")


def main():
    categories = load_categories()
    valid_ids = {c["id"] for c in categories}
    db = json.loads(PAPERS_FILE.read_text())
    existing_ids = {p["id"] for p in db["papers"]}

    # 1-2. Gather from all sources and merge.
    candidates = merge_candidates(fetch_arxiv(), fetch_huggingface())
    new_candidates = [c for c in candidates if c["id"] not in existing_ids]
    log(f"{len(new_candidates)} merged candidate(s) not already in the list.")

    # 3. Rank by popularity, then take the per-run budget.
    new_candidates = rank_candidates(new_candidates)[:MAX_CANDIDATES]

    token = os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")
    base_url = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com").strip()

    if not token:
        log("No ANTHROPIC_AUTH_TOKEN set; skipping LLM step (dry run). Nothing written.")
        log(f"Would have evaluated {len(new_candidates)} candidate(s).")
        return 0

    # 4. Classify with the LLM.
    accepted = []
    errors = 0
    for i, cand in enumerate(new_candidates, 1):
        pop = f" ▲{cand['upvotes']}" if cand.get("upvotes") else ""
        log(f"[{i}/{len(new_candidates)}]{pop} {cand['title'][:66]}...")
        try:
            result = classify(cand, categories, token, base_url, valid_ids)
        except LLMError:
            errors += 1
            result = None
        if result:
            accepted.append(result)
            log(f"  ✓ accepted -> {result['category']}")
        time.sleep(1)  # gentle pacing

    # 5. Failure alerting: if there were candidates but every one errored, the
    # endpoint is almost certainly down — fail the run so the owner is notified.
    if new_candidates and errors == len(new_candidates):
        log(f"ERROR: all {errors} candidate(s) failed to evaluate. LLM endpoint down?")
        emit_gh_output(added_count=0, error_count=errors, status="llm_failure")
        return 2

    if errors:
        log(f"Note: {errors} candidate(s) errored out (kept the rest).")

    if not accepted:
        log("No new relevant papers accepted. Data unchanged.")
        emit_gh_output(added_count=0, error_count=errors, status="no_new")
        return 0

    db["papers"].extend(accepted)
    db["meta"]["lastUpdated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    PAPERS_FILE.write_text(json.dumps(db, ensure_ascii=False, indent=2) + "\n")
    log(f"Added {len(accepted)} paper(s). Total now {len(db['papers'])}.")

    titles = "; ".join(p["title"] for p in accepted)
    emit_gh_output(added_count=len(accepted), error_count=errors,
                   status="added", added_titles=titles)
    return 0


if __name__ == "__main__":
    sys.exit(main())
