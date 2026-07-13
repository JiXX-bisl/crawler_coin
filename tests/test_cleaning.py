# -*- coding: utf-8 -*-
import json
import tempfile
import unittest
from pathlib import Path

from crawler.cleaning.pipeline import CleaningPipeline, clean_jsonl, load_rules


RULES = load_rules(Path(__file__).parents[1] / "configs" / "data_cleaning_rules.json")


class CleaningPipelineTests(unittest.TestCase):
    def test_illegal_case_labels_and_navigation_removal(self):
        pipeline = CleaningPipeline(RULES, dataset="illegal_cases", min_chars=20)
        record = {
            "doc_id": "case-1",
            "source_id": "source_001",
            "status": "ok",
            "title": "USDT 洗钱案",
            "content": "首页\n登录\n字号\n大\n嫌疑人通过 USDT 跑分实施洗钱活动，涉案虚拟货币已被依法处置。",
        }
        cleaned, audit = pipeline.clean_record(record)
        self.assertEqual(audit["status"], "accepted")
        self.assertEqual(cleaned["label"], ["犯罪类型/洗钱", "涉币资产/USDT", "涉币资产/虚拟货币", "交易方式/跑分"])
        self.assertIn("标题：USDT 洗钱案", cleaned["content"])
        self.assertNotIn("首页", cleaned["content"])
        self.assertNotIn("字号", cleaned["content"])

    def test_title_is_not_repeated_when_present_in_content(self):
        pipeline = CleaningPipeline(RULES, dataset="illegal_cases", min_chars=20)
        record = {
            "doc_id": "case-title",
            "status": "ok",
            "title": "虚拟货币洗钱案件",
            "content": "虚拟货币洗钱案件\n犯罪分子使用虚拟货币实施洗钱活动，案件事实已经查明。",
        }
        cleaned, _ = pipeline.clean_record(record)
        self.assertEqual(cleaned["content"].count("虚拟货币洗钱案件"), 1)
        self.assertTrue(cleaned["content"].startswith("标题：虚拟货币洗钱案件"))

    def test_leading_navigation_shell_is_removed_without_touching_body_words(self):
        pipeline = CleaningPipeline(RULES, dataset="illegal_cases", min_chars=20)
        record = {
            "doc_id": "navigation",
            "status": "ok",
            "title": "\u865a\u62df\u8d27\u5e01\u6d17\u94b1\u6848",
            "content": "\u7f51\u7ad9\u9996\u9875\n\u65b0\u95fb\u8d44\u8baf\n\u673a\u6784\u8bbe\u7f6e\n\u8054\u7cfb\u6211\u4eec\n\u72af\u7f6a\u5206\u5b50\u4f7f\u7528\u865a\u62df\u8d27\u5e01\u5b9e\u65bd\u6d17\u94b1\uff0c\u6848\u4ef6\u8bc1\u636e\u660e\u786e\u4e14\u5df2\u4f9d\u6cd5\u5904\u7f6e\u3002",
        }
        cleaned, _ = pipeline.clean_record(record)
        self.assertNotIn("\u7f51\u7ad9\u9996\u9875", cleaned["content"])
        self.assertNotIn("\u65b0\u95fb\u8d44\u8baf", cleaned["content"])
        self.assertIn("\u72af\u7f6a\u5206\u5b50", cleaned["content"])

    def test_knowledge_labels_do_not_use_case_taxonomy(self):
        pipeline = CleaningPipeline(RULES, dataset="coin_knowledge", min_chars=20)
        record = {
            "doc_id": "knowledge-1",
            "status": "ok",
            "title": "Bitcoin transaction basics",
            "content": "Bitcoin uses a blockchain transaction model. A wallet signs a transaction.",
        }
        cleaned, _ = pipeline.clean_record(record)
        self.assertIn("知识/比特币", cleaned["label"])
        self.assertIn("知识/区块链", cleaned["label"])
        self.assertIn("知识/钱包", cleaned["label"])
        self.assertNotIn("犯罪类型/洗钱", cleaned["label"])

    def test_short_english_token_does_not_match_inside_another_word(self):
        pipeline = CleaningPipeline(RULES, dataset="coin_knowledge", min_chars=20)
        record = {
            "doc_id": "knowledge-boundary",
            "status": "ok",
            "title": "Transaction overview",
            "content": "The transaction is recorded on a blockchain and signed by a wallet.",
        }
        cleaned, _ = pipeline.clean_record(record)
        self.assertNotIn("\u77e5\u8bc6/\u4ee5\u592a\u574a", cleaned["label"])
        self.assertIn("\u77e5\u8bc6/\u533a\u5757\u94fe", cleaned["label"])

    def test_short_and_duplicate_records_go_to_audit(self):
        pipeline = CleaningPipeline(RULES, dataset="illegal_cases", min_chars=20)
        records = [
            {"doc_id": "one", "source_id": "a", "status": "ok", "title": "比特币洗钱", "content": "比特币洗钱案件的完整事实说明，包含虚拟货币转账证据。"},
            {"doc_id": "two", "source_id": "b", "status": "ok", "title": "同一案件的转载标题", "content": "比特币洗钱案件的完整事实说明，包含虚拟货币转账证据。"},
            {"doc_id": "three", "source_id": "c", "status": "ok", "title": "USDT", "content": "USDT"},
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "records.jsonl"
            with source.open("w", encoding="utf-8") as handle:
                for record in records:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            report = clean_jsonl(source, root / "cleaned", pipeline)
            accepted = [json.loads(line) for line in (root / "cleaned" / "cleaned_records.jsonl").read_text(encoding="utf-8").splitlines()]
            audit = [json.loads(line) for line in (root / "cleaned" / "cleaning_audit.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(report["accepted_records"], 1)
        self.assertEqual(report["duplicate_records"], 1)
        self.assertEqual(len(accepted), 1)
        self.assertEqual(set(accepted[0]), {"label", "content"})
        self.assertIn("duplicate_cleaned_content", audit[1]["reasons"])
        self.assertIn("content_too_short", audit[2]["reasons"])


if __name__ == "__main__":
    unittest.main()
