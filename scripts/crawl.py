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
  4. Ask GPT-5.6 to judge relevance, pick a category, and write a bilingual
     (EN/ZH) one-line summary for each new candidate.
  5. Append accepted papers to data/papers.json and record per-run paper,
     token, and estimated cost statistics in data/crawl-stats.json.

Environment variables:
  OPENAI_API_KEY        (required for LLM step) OpenAI API key
  OPENAI_BASE_URL       (optional) defaults to https://api.openai.com/v1
  OPENAI_MODEL          (optional) defaults to gpt-5.6-terra
  OPENAI_REASONING_EFFORT (optional) defaults to low
  MAX_CANDIDATES        (optional) cap papers sent to the LLM per run (default 40)
  CRAWL_DAYS            (optional) how many days back to search (default 3)
  DISABLE_HF            (optional) set to "1" to skip the Hugging Face source

Exit codes:
  0  success (papers added, or nothing new / relevant)
  2  every candidate failed due to errors (likely LLM endpoint down) — this
     marks the GitHub Action run red so the owner gets a failure notification.

Without an API key the script still runs: it fetches, dedupes, and ranks, but
skips the LLM step. GitHub Actions records the dry run; local runs leave data
unchanged unless RECORD_CRAWL_STATS=1 is set.
"""

from collections import Counter
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
STATS_FILE = DATA_DIR / "crawl-stats.json"

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

def env(name, default=""):
    """os.environ.get, but treats an empty/whitespace value as unset. CI systems
    (e.g. GitHub Actions `${{ vars.X }}`) inject empty strings for undefined
    variables, which would otherwise override sensible defaults."""
    val = os.environ.get(name, "")
    return val.strip() if val and val.strip() else default


MAX_CANDIDATES = int(env("MAX_CANDIDATES", "40"))
CRAWL_DAYS = int(env("CRAWL_DAYS", "3"))
MODEL = env("OPENAI_MODEL", "gpt-5.6-terra")
REASONING_EFFORT = env("OPENAI_REASONING_EFFORT", "low")
DISABLE_HF = env("DISABLE_HF") == "1"

# Standard text-token prices in USD per 1M tokens. Keep these rates attached to
# each run so historical estimates remain stable if prices change later.
# Source: https://developers.openai.com/api/docs/models/gpt-5.6-terra
MODEL_PRICING_PER_MILLION = {
    "gpt-5.6": {"input": 4.0, "cached_input": 0.4, "output": 20.0},
    "gpt-5.6-sol": {"input": 4.0, "cached_input": 0.4, "output": 20.0},
    "gpt-5.6-terra": {"input": 2.0, "cached_input": 0.2, "output": 12.0},
    "gpt-5.6-luna": {"input": 0.2, "cached_input": 0.02, "output": 1.2},
}

VALID_REASONING_EFFORTS = {"none", "low", "medium", "high", "xhigh", "max"}


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


def responses_endpoint(base_url):
    """Accept both https://api.openai.com and .../v1 style base URLs."""
    base = base_url.rstrip("/")
    return base + "/responses" if base.endswith("/v1") else base + "/v1/responses"


def response_output_text(payload):
    """Extract assistant text from a raw Responses API payload."""
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"].strip()
    chunks = []
    for item in payload.get("output", []):
        for part in item.get("content", []):
            if part.get("type") == "output_text" and part.get("text"):
                chunks.append(part["text"])
    return "".join(chunks).strip()


def pricing_for_model(model):
    if model in MODEL_PRICING_PER_MILLION:
        return dict(MODEL_PRICING_PER_MILLION[model])
    # Snapshot ids inherit the base model's rate. Match the most specific slug
    # first so gpt-5.6-terra-* never falls through to the gpt-5.6 alias.
    for slug in sorted(MODEL_PRICING_PER_MILLION, key=len, reverse=True):
        if model.startswith(slug + "-"):
            return dict(MODEL_PRICING_PER_MILLION[slug])
    return None


def empty_usage():
    return {
        "apiCalls": 0,
        "inputTokens": 0,
        "cachedInputTokens": 0,
        "cacheWriteTokens": 0,
        "outputTokens": 0,
        "reasoningTokens": 0,
        "totalTokens": 0,
        "estimatedCostUsd": 0.0,
        "costAvailable": True,
    }


def accumulate_usage(totals, usage, model):
    """Accumulate one Responses API usage object and its estimated token cost."""
    usage = usage or {}
    input_details = usage.get("input_tokens_details") or {}
    output_details = usage.get("output_tokens_details") or {}
    input_tokens = int(usage.get("input_tokens") or 0)
    cached_tokens = int(input_details.get("cached_tokens") or 0)
    cache_write_tokens = int(input_details.get("cache_write_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or input_tokens + output_tokens)

    totals["apiCalls"] += 1
    totals["inputTokens"] += input_tokens
    totals["cachedInputTokens"] += cached_tokens
    totals["cacheWriteTokens"] += cache_write_tokens
    totals["outputTokens"] += output_tokens
    totals["reasoningTokens"] += int(output_details.get("reasoning_tokens") or 0)
    totals["totalTokens"] += total_tokens

    pricing = pricing_for_model(model)
    if not pricing:
        totals["costAvailable"] = False
        return

    regular_input = max(0, input_tokens - cached_tokens - cache_write_tokens)
    # GPT-5.6 prompts over 272K tokens use a 2x input / 1.5x output multiplier.
    input_multiplier = 2.0 if input_tokens > 272_000 else 1.0
    output_multiplier = 1.5 if input_tokens > 272_000 else 1.0
    micro_cost = (
        regular_input * pricing["input"] * input_multiplier
        + cached_tokens * pricing["cached_input"] * input_multiplier
        + cache_write_tokens * pricing["input"] * 1.25 * input_multiplier
        + output_tokens * pricing["output"] * output_multiplier
    )
    totals["estimatedCostUsd"] += micro_cost / 1_000_000


def call_openai(prompt, token, base_url, category_ids):
    endpoint = responses_endpoint(base_url)
    category_values = sorted(category_ids) + [None]
    body = json.dumps({
        "model": MODEL,
        "input": prompt,
        "max_output_tokens": 800,
        "reasoning": {"effort": REASONING_EFFORT},
        "text": {
            "verbosity": "low",
            "format": {
                "type": "json_schema",
                "name": "paper_classification",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "relevant": {"type": "boolean"},
                        "category": {"enum": category_values},
                        "summary_en": {"type": ["string", "null"]},
                        "summary_zh": {"type": ["string", "null"]},
                    },
                    "required": ["relevant", "category", "summary_en", "summary_zh"],
                    "additionalProperties": False,
                },
            },
        },
    }).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=body,
        headers={
            "content-type": "application/json",
            "authorization": f"Bearer {token}",
            "User-Agent": "awesome-llm-post-training-crawler/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return response_output_text(payload), payload.get("usage") or {}, payload.get("model") or MODEL


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


def classify(candidate, categories, token, base_url, valid_ids, usage_totals):
    """Return an accepted paper dict, or None if judged irrelevant.

    Raises LLMError if the candidate could not be evaluated after retries, so
    callers can distinguish 'rejected' from 'endpoint is down'."""
    prompt = build_prompt(candidate, categories)
    last_err = None
    for attempt in range(3):
        try:
            raw, usage, response_model = call_openai(prompt, token, base_url, valid_ids)
            accumulate_usage(usage_totals, usage, response_model)
        except Exception as e:  # noqa: BLE001 - network/transient, retry
            last_err = e
            detail = ""
            if hasattr(e, "read"):
                try:
                    detail = " | body: " + e.read().decode("utf-8", "replace")[:200]
                except Exception:  # noqa: BLE001
                    pass
            wait = 2 ** attempt
            log(f"  {candidate['id']}: LLM error ({e}){detail}; retry in {wait}s.")
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


def build_run_record(started_at, status, source_counts, new_count, evaluated_count,
                     accepted, errors, usage_totals):
    finished_at = datetime.now(timezone.utc)
    category_counts = dict(sorted(Counter(p["category"] for p in accepted).items()))
    usage = {k: v for k, v in usage_totals.items() if k != "costAvailable"}
    usage["estimatedCostUsd"] = (
        round(usage_totals["estimatedCostUsd"], 8)
        if usage_totals["costAvailable"] else None
    )
    pricing = pricing_for_model(MODEL)
    if pricing:
        pricing = {
            "inputPerMillion": pricing["input"],
            "cachedInputPerMillion": pricing["cached_input"],
            "cacheWritePerMillion": pricing["input"] * 1.25,
            "outputPerMillion": pricing["output"],
            "currency": "USD",
        }
    run_id = env("GITHUB_RUN_ID", started_at.strftime("local-%Y%m%dT%H%M%SZ"))
    attempt = env("GITHUB_RUN_ATTEMPT")
    if attempt:
        run_id += f"-{attempt}"
    return {
        "runId": run_id,
        "date": finished_at.strftime("%Y-%m-%d"),
        "startedAt": started_at.isoformat().replace("+00:00", "Z"),
        "finishedAt": finished_at.isoformat().replace("+00:00", "Z"),
        "durationSeconds": round((finished_at - started_at).total_seconds(), 2),
        "status": status,
        "model": MODEL,
        "reasoningEffort": REASONING_EFFORT,
        "sources": source_counts,
        "papers": {
            "newCandidates": new_count,
            "evaluated": evaluated_count,
            "added": len(accepted),
            "rejected": max(0, evaluated_count - len(accepted) - errors),
            "errors": errors,
            "byCategory": category_counts,
        },
        "usage": usage,
        "pricing": pricing,
    }


def append_run_stats(run):
    if STATS_FILE.exists():
        try:
            stats = json.loads(STATS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            stats = {}
    else:
        stats = {}
    stats.setdefault("schemaVersion", 1)
    stats.setdefault("runs", []).append(run)
    stats["meta"] = {
        "lastUpdated": run["finishedAt"],
        "currency": "USD",
        "costLabel": "Estimated standard API token cost",
        "pricingSource": "https://developers.openai.com/api/docs/models/gpt-5.6-terra",
    }
    STATS_FILE.write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n")


def main():
    started_at = datetime.now(timezone.utc)
    if REASONING_EFFORT not in VALID_REASONING_EFFORTS:
        log(f"Invalid OPENAI_REASONING_EFFORT={REASONING_EFFORT!r}.")
        return 2

    categories = load_categories()
    valid_ids = {c["id"] for c in categories}
    db = json.loads(PAPERS_FILE.read_text())
    existing_ids = {p["id"] for p in db["papers"]}

    # 1-2. Gather from all sources and merge.
    arxiv_candidates = fetch_arxiv()
    hf_candidates = fetch_huggingface()
    candidates = merge_candidates(arxiv_candidates, hf_candidates)
    new_candidates = [c for c in candidates if c["id"] not in existing_ids]
    new_count = len(new_candidates)
    source_counts = {
        "arxiv": len(arxiv_candidates),
        "huggingFace": len(hf_candidates),
        "merged": len(candidates),
    }
    log(f"{new_count} merged candidate(s) not already in the list.")

    # 3. Rank by popularity, then take the per-run budget.
    new_candidates = rank_candidates(new_candidates)[:MAX_CANDIDATES]

    token = env("OPENAI_API_KEY")
    base_url = env("OPENAI_BASE_URL", "https://api.openai.com/v1")
    usage_totals = empty_usage()

    def finalize(status, accepted, errors, evaluated_count, write_stats=True):
        run = build_run_record(
            started_at, status, source_counts, new_count, evaluated_count,
            accepted, errors, usage_totals,
        )
        if write_stats:
            append_run_stats(run)
        estimated = run["usage"]["estimatedCostUsd"]
        emit_gh_output(
            added_count=len(accepted),
            evaluated_count=evaluated_count,
            error_count=errors,
            status=status,
            model=MODEL,
            total_tokens=run["usage"]["totalTokens"],
            input_tokens=run["usage"]["inputTokens"],
            output_tokens=run["usage"]["outputTokens"],
            estimated_cost_usd="n/a" if estimated is None else f"{estimated:.8f}",
            added_titles="; ".join(p["title"] for p in accepted),
        )
        return run

    if not token:
        log("No OPENAI_API_KEY set; skipping LLM step (dry run).")
        log(f"Would have evaluated {len(new_candidates)} candidate(s).")
        record_dry_run = env("GITHUB_ACTIONS").lower() == "true" or env("RECORD_CRAWL_STATS") == "1"
        finalize("dry_run", [], 0, 0, write_stats=record_dry_run)
        return 0

    # 4. Classify with the LLM.
    accepted = []
    errors = 0
    for i, cand in enumerate(new_candidates, 1):
        pop = f" ▲{cand['upvotes']}" if cand.get("upvotes") else ""
        log(f"[{i}/{len(new_candidates)}]{pop} {cand['title'][:66]}...")
        try:
            result = classify(cand, categories, token, base_url, valid_ids, usage_totals)
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
        finalize("llm_failure", [], errors, len(new_candidates))
        return 2

    if errors:
        log(f"Note: {errors} candidate(s) errored out (kept the rest).")

    if not accepted:
        log("No new relevant papers accepted. Data unchanged.")
        run = finalize("no_new", [], errors, len(new_candidates))
        log(f"Usage: {run['usage']['totalTokens']:,} tokens, estimated ${run['usage']['estimatedCostUsd'] or 0:.6f}.")
        return 0

    db["papers"].extend(accepted)
    db["meta"]["lastUpdated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    PAPERS_FILE.write_text(json.dumps(db, ensure_ascii=False, indent=2) + "\n")
    log(f"Added {len(accepted)} paper(s). Total now {len(db['papers'])}.")

    run = finalize("added", accepted, errors, len(new_candidates))
    log(f"Usage: {run['usage']['totalTokens']:,} tokens, estimated ${run['usage']['estimatedCostUsd'] or 0:.6f}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
