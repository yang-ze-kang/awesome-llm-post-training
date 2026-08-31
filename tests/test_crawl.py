import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from scripts import crawl


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class CrawlUsageTests(unittest.TestCase):
    def test_responses_endpoint_accepts_base_url_with_or_without_v1(self):
        self.assertEqual(
            crawl.responses_endpoint("https://api.openai.com/v1"),
            "https://api.openai.com/v1/responses",
        )
        self.assertEqual(
            crawl.responses_endpoint("https://example.test"),
            "https://example.test/v1/responses",
        )

    def test_accumulate_usage_prices_cached_and_cache_write_tokens(self):
        totals = crawl.empty_usage()
        crawl.accumulate_usage(
            totals,
            {
                "input_tokens": 1_000,
                "input_tokens_details": {
                    "cached_tokens": 200,
                    "cache_write_tokens": 100,
                },
                "output_tokens": 50,
                "output_tokens_details": {"reasoning_tokens": 10},
                "total_tokens": 1_050,
            },
            "gpt-5.6-terra",
        )
        self.assertEqual(totals["apiCalls"], 1)
        self.assertEqual(totals["reasoningTokens"], 10)
        self.assertEqual(totals["totalTokens"], 1_050)
        self.assertAlmostEqual(totals["estimatedCostUsd"], 0.00229)

    def test_unknown_model_keeps_tokens_but_marks_cost_unavailable(self):
        totals = crawl.empty_usage()
        crawl.accumulate_usage(totals, {"input_tokens": 10, "output_tokens": 5}, "custom-model")
        self.assertEqual(totals["totalTokens"], 15)
        self.assertFalse(totals["costAvailable"])

    def test_call_openai_uses_responses_api_and_extracts_usage(self):
        payload = {
            "model": "gpt-5.6-terra",
            "output": [{"content": [{"type": "output_text", "text": '{"relevant":false,"category":null,"summary_en":null,"summary_zh":null}'}]}],
            "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
        }
        with patch("scripts.crawl.urllib.request.urlopen", return_value=FakeResponse(payload)) as mocked:
            text, usage, model = crawl.call_openai(
                "classify this",
                "test-key",
                "https://api.openai.com/v1",
                {"reasoning", "dpo"},
            )
        request = mocked.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual(request.full_url, "https://api.openai.com/v1/responses")
        self.assertEqual(request.headers["Authorization"], "Bearer test-key")
        self.assertEqual(body["model"], "gpt-5.6-terra")
        self.assertEqual(body["text"]["format"]["type"], "json_schema")
        self.assertIn(None, body["text"]["format"]["schema"]["properties"]["category"]["enum"])
        self.assertIn('"relevant":false', text)
        self.assertEqual(usage["total_tokens"], 120)
        self.assertEqual(model, "gpt-5.6-terra")

    def test_run_record_includes_category_breakdown(self):
        started = datetime(2026, 8, 31, tzinfo=timezone.utc)
        usage = crawl.empty_usage()
        accepted = [
            {"title": "A", "category": "dpo"},
            {"title": "B", "category": "dpo"},
            {"title": "C", "category": "reasoning"},
        ]
        record = crawl.build_run_record(
            started,
            "added",
            {"arxiv": 9, "huggingFace": 4, "merged": 10},
            7,
            6,
            accepted,
            1,
            usage,
        )
        self.assertEqual(record["papers"]["added"], 3)
        self.assertEqual(record["papers"]["rejected"], 2)
        self.assertEqual(record["papers"]["byCategory"], {"dpo": 2, "reasoning": 1})
        self.assertEqual(record["pricing"]["inputPerMillion"], 2.0)

    def test_main_appends_paper_and_daily_statistics(self):
        candidate = {
            "id": "2608.12345",
            "title": "A Better Preference Optimizer",
            "abstract": "A direct preference optimization method for language models.",
            "date": "2026-08-31",
            "url": "https://arxiv.org/abs/2608.12345",
            "upvotes": 12,
            "source": "arxiv",
        }
        response = json.dumps({
            "relevant": True,
            "category": "dpo",
            "summary_en": "Introduces a better preference optimizer.",
            "summary_zh": "提出一种更好的偏好优化方法。",
        })
        usage = {"input_tokens": 500, "output_tokens": 50, "total_tokens": 550}

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            papers_file = root / "papers.json"
            categories_file = root / "categories.json"
            stats_file = root / "crawl-stats.json"
            papers_file.write_text(json.dumps({"meta": {"lastUpdated": None}, "papers": []}))
            categories_file.write_text(json.dumps({
                "groups": [{
                    "name": {"en": "Preference optimization", "zh": "偏好优化"},
                    "categories": [{
                        "id": "dpo",
                        "name": {"en": "DPO", "zh": "DPO"},
                        "desc": {"en": "Direct preference optimization", "zh": "直接偏好优化"},
                    }],
                }],
            }))

            with (
                patch.object(crawl, "PAPERS_FILE", papers_file),
                patch.object(crawl, "CATEGORIES_FILE", categories_file),
                patch.object(crawl, "STATS_FILE", stats_file),
                patch.object(crawl, "fetch_arxiv", return_value=[candidate]),
                patch.object(crawl, "fetch_huggingface", return_value=[]),
                patch.object(crawl, "call_openai", return_value=(response, usage, "gpt-5.6-terra")),
                patch.object(crawl.time, "sleep"),
                patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}),
            ):
                self.assertEqual(crawl.main(), 0)

            papers = json.loads(papers_file.read_text())
            stats = json.loads(stats_file.read_text())
            self.assertEqual(papers["papers"][0]["id"], candidate["id"])
            self.assertEqual(stats["runs"][0]["papers"]["added"], 1)
            self.assertEqual(stats["runs"][0]["papers"]["byCategory"], {"dpo": 1})
            self.assertEqual(stats["runs"][0]["usage"]["totalTokens"], 550)
            self.assertAlmostEqual(stats["runs"][0]["usage"]["estimatedCostUsd"], 0.0016)


if __name__ == "__main__":
    unittest.main()
