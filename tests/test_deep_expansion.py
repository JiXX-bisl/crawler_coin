# -*- coding: utf-8 -*-
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from crawler.core.engine import CrawlEngine
from crawler.core.models import CrawlConfig, DiscoveryConfig, OutputConfig, RuntimeConfig, SourceConfig, UrlRules


class FakeFetcher:
    calls = []

    def __init__(self, *args, **kwargs):
        pass

    def fetch(self, url):
        self.__class__.calls.append(url)
        pages = {
            "https://example.test/start.html": b"""<html><body>
                <a href=\"/child.html\">Bitcoin guide</a>
                <a href=\"/manual.pdf\">Bitcoin PDF</a>
                <a href=\"https://github.com/owner/repo\">Bitcoin source</a>
                <a href=\"/privacy\">Privacy</a>
                <a href=\"https://outside.test/article.html\">Bitcoin outside</a>
            </body></html>""",
            "https://example.test/child.html": b'<html><body>Bitcoin child <a href="/grandchild.html">Bitcoin grandchild</a></body></html>',
            "https://example.test/manual.pdf": b"%PDF-not-a-real-document",
            "https://github.com/owner/repo": b'<html><body>Bitcoin source <a href="/next.html">Bitcoin next</a></body></html>',
            "https://example.test/only.pdf": b"%PDF-not-a-real-document",
        }
        return {
            "ok": True,
            "status_code": 200,
            "final_url": url,
            "headers": {"Content-Type": "application/pdf" if url.endswith(".pdf") else "text/html; charset=utf-8"},
            "body": pages[url],
            "retry_count": 0,
        }


class AllowAllRobots:
    def __init__(self, *args, **kwargs):
        pass

    def allowed(self, url):
        return True, "robots_allowed"


def make_source(start_url="https://example.test/start.html"):
    return SourceConfig(
        id="test_source",
        enabled=True,
        start_urls=[start_url],
        parser="auto",
        discoverer="html_links",
        max_depth=1,
        max_pages=5,
        url_rules=UrlRules(
            same_domain=True,
            allow_patterns=[r"start\.html", r"manual\.pdf", r"github\.com/owner/repo"],
            allowed_domains=["github.com"],
        ),
        discovery=DiscoveryConfig(enabled=True, positive_keywords=["bitcoin"], min_link_score=0.6),
    )


def run_source(mode, source):
    output_dir = tempfile.mkdtemp()
    config = CrawlConfig(
        job={"id": "coin_knowledge"},
        runtime=RuntimeConfig(request_interval_seconds=0, respect_robots_txt=False, expansion_mode=mode),
        output=OutputConfig(directory=output_dir),
        sources=[source],
    )
    FakeFetcher.calls = []
    with patch("crawler.core.engine.HttpFetcher", FakeFetcher), patch("crawler.core.engine.RobotGuard", AllowAllRobots), patch(
        "crawler.core.engine.parse_pdf", return_value={"parser": "pdf", "pages": [], "text": "", "quality_flags": []}
    ):
        CrawlEngine(config, progress=False).run()
    records = [json.loads(line) for line in Path(output_dir, "records.jsonl").read_text(encoding="utf-8").splitlines()]
    expansion = json.loads(Path(output_dir, "expansion_report.json").read_text(encoding="utf-8"))["sources"][0]
    return records, expansion, FakeFetcher.calls


class DeepExpansionTests(unittest.TestCase):
    def test_controlled_expands_relevant_html_and_stops_terminal_nodes(self):
        records, expansion, calls = run_source("controlled", make_source())
        urls = {record["url"] for record in records}
        self.assertIn("https://example.test/child.html", urls)
        self.assertIn("https://example.test/manual.pdf", urls)
        self.assertIn("https://github.com/owner/repo", urls)
        self.assertNotIn("https://example.test/grandchild.html", urls)
        self.assertNotIn("https://github.com/next.html", urls)
        self.assertNotIn("https://outside.test/article.html", urls)
        self.assertEqual(expansion["html_candidates_accepted"], 1)
        self.assertEqual(expansion["terminal_candidates_accepted"], 2)
        self.assertIn("domain_not_allowed", expansion["filtered_reasons"])
        self.assertEqual(len(calls), 4)

    def test_strict_does_not_bypass_allow_regex(self):
        records, expansion, _ = run_source("strict", make_source())
        urls = {record["url"] for record in records}
        self.assertNotIn("https://example.test/child.html", urls)
        self.assertIn("https://example.test/manual.pdf", urls)
        self.assertIn("https://github.com/owner/repo", urls)
        self.assertGreater(expansion["filtered_reasons"].get("allow_regex_miss", 0), 0)

    def test_pdf_seed_is_terminal_even_when_discoverer_is_enabled(self):
        source = make_source("https://example.test/only.pdf")
        source.max_depth = 2
        source.max_pages = 3
        source.url_rules.allow_patterns = [r"only\.pdf"]
        records, expansion, calls = run_source("controlled", source)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["node_type"], "pdf")
        self.assertEqual(expansion["candidates_discovered"], 0)
        self.assertIn("non_html_seed", expansion["unable_reasons"])
        self.assertEqual(calls, ["https://example.test/only.pdf"])


class CaseFetcher:
    calls = []

    def __init__(self, *args, **kwargs):
        pass

    def fetch(self, url):
        self.__class__.calls.append(url)
        pages = {
            "https://case.test/start.html": b'<html><body><a href="/cases/2024-case.html">Crypto case judgment</a><a href="/grzx/myWorkbench">Crypto case portal</a><a href="/notice.html">Crypto announcement</a></body></html>',
            "https://case.test/cases/2024-case.html": ("<html><body><h1>Case</h1>\uff082024\uff09\u4eac0105\u5211\u521d123\u53f7 \u5317\u4eac\u5e02\u671d\u9633\u533a\u4eba\u6c11\u6cd5\u9662 \u8bc8\u9a97\u7f6a \u6d17\u94b1\u7f6a \u4eba\u6c11\u5e01100\u4e07\u5143 2024\u5e745\u67081\u65e5 \u300a\u4e2d\u534e\u4eba\u6c11\u5171\u548c\u56fd\u5211\u6cd5\u300b\u7b2c191\u6761 \u865a\u62df\u8d27\u5e01 USDT</body></html>").encode("utf-8"),
        }
        return {"ok": True, "status_code": 200, "final_url": url, "headers": {"Content-Type": "text/html; charset=utf-8"}, "body": pages[url], "retry_count": 0}


def run_case_source(source):
    output_dir = tempfile.mkdtemp()
    config = CrawlConfig(job={"id": "illegal_cases"}, runtime=RuntimeConfig(request_interval_seconds=0, respect_robots_txt=False, expansion_mode="controlled"), output=OutputConfig(directory=output_dir), sources=[source])
    CaseFetcher.calls = []
    with patch("crawler.core.engine.HttpFetcher", CaseFetcher), patch("crawler.core.engine.RobotGuard", AllowAllRobots):
        CrawlEngine(config, progress=False).run()
    records = [json.loads(line) for line in Path(output_dir, "records.jsonl").read_text(encoding="utf-8").splitlines()]
    expansion = json.loads(Path(output_dir, "expansion_report.json").read_text(encoding="utf-8"))["sources"][0]
    return records, expansion, CaseFetcher.calls


class CasePrecisionTests(unittest.TestCase):
    def test_case_expansion_requires_detail_signal_and_extracts_case_facts(self):
        source = SourceConfig(
            id="case_topic", enabled=True, start_urls=["https://case.test/start.html"], parser="auto", discoverer="html_links", source_role="topic_page", max_depth=1, max_pages=10,
            url_rules=UrlRules(same_domain=True, allow_patterns=[r"start\.html"]),
            discovery=DiscoveryConfig(enabled=True, positive_keywords=["crypto"], min_link_score=0.65),
        )
        records, expansion, calls = run_case_source(source)
        urls = {record["url"] for record in records}
        self.assertIn("https://case.test/cases/2024-case.html", urls)
        self.assertNotIn("https://case.test/grzx/myWorkbench", urls)
        self.assertNotIn("https://case.test/notice.html", urls)
        self.assertIn("negative_topic_signal", expansion["filtered_reasons"])
        self.assertIn("case_detail_signal_missing", expansion["filtered_reasons"])
        facts = next(record["case_facts"] for record in records if record["url"].endswith("2024-case.html"))
        self.assertTrue(facts["case_numbers"])
        self.assertIn("\u8bc8\u9a97\u7f6a", facts["crime_types"])
        self.assertIn("\u6d17\u94b1\u7f6a", facts["crime_types"])
        self.assertTrue(facts["organizations"])
        self.assertTrue(facts["amounts"])
        self.assertTrue(facts["legal_articles"])
        self.assertEqual(calls, ["https://case.test/start.html", "https://case.test/cases/2024-case.html"])

    def test_duplicate_url_writes_source_association(self):
        output_dir = tempfile.mkdtemp()
        source_one = SourceConfig(id="one", enabled=True, start_urls=["https://example.test/start.html"], parser="auto", max_depth=0, max_pages=1)
        source_two = SourceConfig(id="two", enabled=True, start_urls=["https://example.test/start.html"], parser="auto", max_depth=0, max_pages=1)
        config = CrawlConfig(job={"id": "coin_knowledge"}, runtime=RuntimeConfig(request_interval_seconds=0, respect_robots_txt=False), output=OutputConfig(directory=output_dir), sources=[source_one, source_two])
        FakeFetcher.calls = []
        with patch("crawler.core.engine.HttpFetcher", FakeFetcher), patch("crawler.core.engine.RobotGuard", AllowAllRobots):
            CrawlEngine(config, progress=False).run()
        associations = [json.loads(line) for line in Path(output_dir, "source_associations.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(associations), 1)
        self.assertEqual(associations[0]["relation"], "duplicate_url")
        self.assertEqual(associations[0]["source_id"], "two")


if __name__ == "__main__":
    unittest.main()
