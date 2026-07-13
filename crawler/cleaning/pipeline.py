# -*- coding: utf-8 -*-
"""Reusable cleaning pipeline for crawler JSONL records."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


def load_rules(path: str | Path) -> Dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


class CleaningPipeline:
    def __init__(self, rules: Dict[str, Any], dataset: str = "auto", min_chars: int = 200):
        self.rules = rules
        self.dataset = dataset
        self.min_chars = min_chars
        text_rules = rules["text_cleaning"]
        self._line_patterns = [re.compile(pattern, re.IGNORECASE) for pattern in text_rules["drop_line_patterns"]]
        self._inline_patterns = [re.compile(pattern, re.IGNORECASE) for pattern in text_rules["drop_inline_patterns"]]
        self._header_navigation_terms = [term.lower() for term in text_rules.get("header_navigation_terms", [])]

    def clean_record(self, record: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
        dataset = self._resolve_dataset(record)
        raw_content = record.get("content") or ""
        title = self._clean_title(record.get("title") or "")
        body = self._clean_text(raw_content)
        body = self._remove_pdf_repeated_lines(body, record.get("pdf_pages") or [])
        content = self._prepend_title(title, body)
        labels = self._labels(dataset, content)
        reasons = self._rejection_reasons(record, content, labels)
        content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest() if body else None
        audit = {
            "doc_id": record.get("doc_id"),
            "source_id": record.get("source_id"),
            "dataset": dataset,
            "status": "accepted" if not reasons else "rejected",
            "reasons": reasons,
            "original_char_count": len(raw_content),
            "cleaned_char_count": len(content),
            "labels": labels,
            "cleaned_content_hash": content_hash,
        }
        if reasons:
            return None, audit
        return {"label": labels, "content": content}, audit

    def _resolve_dataset(self, record: Dict[str, Any]) -> str:
        if self.dataset != "auto":
            return self.dataset
        job_id = (record.get("job_id") or "").lower()
        metadata = record.get("metadata") or {}
        if "illegal" in job_id or "illegal_case" in metadata:
            return "illegal_cases"
        if "knowledge" in job_id or "knowledge" in metadata:
            return "coin_knowledge"
        return "unknown"

    def _clean_title(self, title: str) -> str:
        title = self._normalize(title)
        return re.sub(r"\s+", " ", title).strip(" -|_")

    def _clean_text(self, text: str) -> str:
        text = self._normalize(text)
        for pattern in self._inline_patterns:
            text = pattern.sub("", text)
        cleaned_lines: List[str] = []
        previous = None
        for raw_line in text.split("\n"):
            line = re.sub(r"\s+", " ", raw_line).strip()
            if not line or self._should_drop_line(line):
                continue
            if line == previous:
                continue
            cleaned_lines.append(line)
            previous = line
        return "\n".join(self._trim_leading_chrome(cleaned_lines)).strip()

    def _trim_leading_chrome(self, lines: List[str]) -> List[str]:
        if not self._header_navigation_terms or len(lines) < 3:
            return lines
        leading_lines = lines[:40]
        marker_count = sum(
            any(term in line.lower() for term in self._header_navigation_terms)
            for line in leading_lines
        )
        if marker_count < 2:
            return lines
        for index, line in enumerate(lines[:80]):
            if len(line) >= 20:
                return lines[index:]
        return lines

    @staticmethod
    def _normalize(text: str) -> str:
        text = unicodedata.normalize("NFKC", text or "")
        text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
        text = re.sub(r"[\u200b-\u200f\u202a-\u202e\ufeff]", "", text)
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _should_drop_line(self, line: str) -> bool:
        if len(line) <= 4 and re.fullmatch(r"[\[\]<>|/\\·•—–\- ]+", line):
            return True
        return any(pattern.search(line) for pattern in self._line_patterns)

    def _remove_pdf_repeated_lines(self, content: str, pages: List[Dict[str, Any]]) -> str:
        if len(pages) < 2 or not content:
            return content
        counts: Counter[str] = Counter()
        for page in pages:
            seen_on_page = set()
            for line in self._clean_text(page.get("text") or "").split("\n"):
                if 2 <= len(line) <= 120:
                    seen_on_page.add(line)
            counts.update(seen_on_page)
        repeated = {line for line, count in counts.items() if count >= 2}
        if not repeated:
            return content
        return "\n".join(line for line in content.split("\n") if line not in repeated).strip()

    @staticmethod
    def _prepend_title(title: str, content: str) -> str:
        if not title:
            return content
        normalized_title = re.sub(r"\s+", "", title)
        lines = content.split("\n")
        for index, line in enumerate(lines[:12]):
            normalized_line = re.sub(r"\s+", "", line)
            exact_match = normalized_line == normalized_title
            contained_match = (
                len(normalized_title) >= 12
                and len(normalized_line) >= 12
                and (normalized_line in normalized_title or normalized_title in normalized_line)
            )
            if exact_match or contained_match:
                lines.pop(index)
                break
        body = "\n".join(lines).strip()
        return f"标题：{title}\n\n{body}".strip()


    def _labels(self, dataset: str, text: str) -> List[str]:
        taxonomy = self.rules["label_taxonomy"].get(dataset, {})
        labels: List[str] = []
        text_lower = text.lower()
        for facet in taxonomy.get("facets", []):
            for label, patterns in facet.get("labels", {}).items():
                if any(self._matches(pattern, text, text_lower) for pattern in patterns):
                    labels.append(label)
        return list(dict.fromkeys(labels))

    @staticmethod
    def _matches(pattern: str, text: str, text_lower: str) -> bool:
        if pattern.startswith("re:"):
            return bool(re.search(pattern[3:], text, re.IGNORECASE))
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 .-]*", pattern):
            return bool(re.search(r"(?<![A-Za-z0-9])" + re.escape(pattern.lower()) + r"(?![A-Za-z0-9])", text_lower))
        return pattern.lower() in text_lower

    def _rejection_reasons(self, record: Dict[str, Any], content: str, labels: List[str]) -> List[str]:
        reasons = []
        if record.get("status") not in (None, "ok"):
            reasons.append("crawl_status_not_ok")
        if not content:
            reasons.append("empty_content")
        elif len(content) < self.min_chars:
            reasons.append("content_too_short")
        if not labels:
            reasons.append("no_semantic_label")
        return reasons


def clean_jsonl(input_path: str | Path, output_dir: str | Path, pipeline: CleaningPipeline, dry_run: bool = False) -> Dict[str, Any]:
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    report: Dict[str, Any] = {
        "input": str(input_path), "total_records": 0, "accepted_records": 0, "rejected_records": 0,
        "rejection_reasons": Counter(), "label_distribution": Counter(), "dataset_distribution": Counter(),
        "source_distribution": Counter(), "duplicate_records": 0,
    }
    seen_hashes: Dict[str, str] = {}
    accepted: List[Dict[str, Any]] = []
    audits: List[Dict[str, Any]] = []

    with input_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                report["total_records"] += 1
                report["rejected_records"] += 1
                report["rejection_reasons"]["invalid_json"] += 1
                audits.append({"line_number": line_number, "status": "rejected", "reasons": ["invalid_json"], "error": str(exc)})
                continue

            report["total_records"] += 1
            cleaned, audit = pipeline.clean_record(record)
            audit["line_number"] = line_number
            report["dataset_distribution"][audit["dataset"]] += 1
            if record.get("source_id"):
                report["source_distribution"][record["source_id"]] += 1

            if cleaned:
                content_hash = audit["cleaned_content_hash"]
                if content_hash in seen_hashes:
                    cleaned = None
                    audit["status"] = "rejected"
                    audit["reasons"] = ["duplicate_cleaned_content"]
                    audit["duplicate_of_doc_id"] = seen_hashes[content_hash]
                    report["duplicate_records"] += 1
                else:
                    seen_hashes[content_hash] = record.get("doc_id") or f"line_{line_number}"

            if cleaned:
                accepted.append(cleaned)
                report["accepted_records"] += 1
                report["label_distribution"].update(cleaned["label"])
            else:
                report["rejected_records"] += 1
                report["rejection_reasons"].update(audit["reasons"])
            audits.append(audit)

    serializable_report = {key: dict(value) if isinstance(value, Counter) else value for key, value in report.items()}
    if not dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
        _write_jsonl(output_dir / "cleaned_records.jsonl", accepted)
        _write_jsonl(output_dir / "cleaning_audit.jsonl", audits)
        with (output_dir / "cleaning_report.json").open("w", encoding="utf-8") as handle:
            json.dump(serializable_report, handle, ensure_ascii=False, indent=2)
    else:
        serializable_report["dry_run"] = True
    return serializable_report


def _write_jsonl(path: Path, records: Iterable[Dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
