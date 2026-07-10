#!/usr/bin/env python3
"""
Filter crawled virtual-currency knowledge records from JSONL.

Rules implemented:
1. Keep only status == "ok".
2. Keep only non-empty content.
3. Keep only char_count >= 500.
4. Deduplicate by URL, preserving merged topic tags.
5. Deduplicate by content_hash.
6. Remove obvious low-value files such as terms, release-notes, test-data, contrib, seed-nodes.
7. Apply caps and lower priority for large sources: DOGE GitHub, Binance GitHub, OKX API.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

PRIORITY_RANK = {"A": 3, "B": 2, "C": 1, "D": 0, "": 0, None: 0}

# Explicitly low-value / crawler-noise paths.
LOW_VALUE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(^|[/_\-.])terms(?:[-_ ]?of[-_ ]?use|[-_ ]?of[-_ ]?service)?($|[/_\-.])", re.I), "low_value_terms"),
    (re.compile(r"release[-_ ]?notes|changelog|release[-_ ]?process", re.I), "low_value_release_notes_or_changelog"),
    (re.compile(r"(^|/)(?:test|tests|qa)(/|$)|test[-_]?data|testgen|fuzzing|benchmark", re.I), "low_value_test_or_benchmark"),
    (re.compile(r"(^|/)contrib(/|$)", re.I), "low_value_contrib"),
    (re.compile(r"seed[-_ ]?nodes|(^|/)seeds(/|$)|nodes_main|nodes_test|dnsseed|chainparamsseeds", re.I), "low_value_seed_nodes"),
    (re.compile(r"(^|/)\.github/|issue_template", re.I), "low_value_repo_meta"),
    (re.compile(r"(^|/)depends(/|$)|build[-_]|gitian|travis[-_]?ci|translation[_-]|translation_process|translation_strings", re.I), "low_value_build_or_localization"),
]

# Large sources: cap retained records and lower priority for retained records.
SOURCE_LIMITS = {
    "doge_github": {
        "max_keep": 12,
        "priority_override": "C",
        "matcher": lambda r: (r.get("github_owner") or "").lower() == "dogecoin" and (r.get("github_repo") or "").lower() == "dogecoin",
    },
    "binance_github": {
        "max_keep": 20,
        "priority_override": "C",
        "matcher": lambda r: (r.get("github_owner") or "").lower() == "binance" and (r.get("github_repo") or "").lower() == "binance-spot-api-docs",
    },
    "okx_api": {
        "max_keep": 10,
        "priority_override": "C",
        "matcher": lambda r: "okx.com" in ((r.get("url") or "") + " " + (r.get("seed_url") or "") + " " + (r.get("source_name") or "")).lower(),
    },
}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                records.append({
                    "_raw_line": line,
                    "_parse_error": str(e),
                    "_line_number": lineno,
                    "filter_decision": "removed",
                    "filter_reasons": ["invalid_json"],
                })
                continue
            obj.setdefault("_line_number", lineno)
            records.append(obj)
    return records


def canonical_url(url: str | None) -> str:
    if not url:
        return ""
    url = str(url).strip()
    try:
        parts = urlsplit(url)
        query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not k.lower().startswith("utm_")]
        path = parts.path
        if path != "/" and path.endswith("/"):
            path = path.rstrip("/")
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))
    except Exception:
        return url.rstrip("/")


def content_hash(record: dict[str, Any]) -> str:
    h = record.get("content_hash")
    if h:
        return str(h)
    content = record.get("content") or ""
    return hashlib.sha256(str(content).encode("utf-8", errors="ignore")).hexdigest()


def merge_topic_tags(records: list[dict[str, Any]]) -> list[str]:
    tags: list[str] = []
    seen = set()
    for r in records:
        raw_tags = r.get("topic_tags")
        if isinstance(raw_tags, list):
            values = [str(x) for x in raw_tags]
        elif isinstance(raw_tags, str) and raw_tags.strip():
            # Works for either JSON-encoded list or semicolon/comma-separated string.
            try:
                loaded = json.loads(raw_tags)
                values = [str(x) for x in loaded] if isinstance(loaded, list) else [raw_tags]
            except Exception:
                values = re.split(r"\s*[;,|]\s*", raw_tags)
        else:
            values = []
        if r.get("level_1") or r.get("level_2"):
            values.append(f"{r.get('level_1','')}/{r.get('level_2','')}")
        for tag in values:
            tag = tag.strip()
            if tag and tag not in seen:
                seen.add(tag)
                tags.append(tag)
    return tags


def score_record(record: dict[str, Any]) -> tuple:
    """Higher score is better for duplicate/source-cap selection."""
    pr = PRIORITY_RANK.get(record.get("source_priority"), 0)
    char_count = int(record.get("char_count") or 0)
    path = (record.get("github_path") or record.get("url") or "").lower()
    title = (record.get("title") or "").lower()

    bonus = 0
    penalty = 0

    # Prefer concise, topical docs over repo-maintenance noise.
    high_value_keywords = [
        "whitepaper", "readme", "getting-started", "getting started", "faq",
        "rest-api", "rest api", "api", "transactions", "transaction", "fees", "fee",
        "bips", "developer", "protocol", "glossary", "filters", "enums", "errors",
        "user-data-stream", "web-socket-api", "web-socket-streams", "general-info",
        "wallet", "account", "smart-contract", "smart contracts", "token",
    ]
    for kw in high_value_keywords:
        if kw in path or kw in title:
            bonus += 2

    # Prefer English/mainnet over CN/testnet duplicates when applying caps.
    if re.search(r"_cn\.md$|/cn($|/)|_cn\b", path):
        penalty += 3
    if "testnet" in path:
        penalty += 2
    if "install" in path or "security" in path or "privatekeynotes" in path:
        penalty += 1
    if path.startswith("src/"):
        penalty += 2

    # Very large docs are useful but should not dominate solely by size.
    size_bucket = min(char_count, 80_000)
    return (pr, bonus - penalty, size_bucket, -(record.get("_line_number") or 0))


def low_value_reason(record: dict[str, Any]) -> str | None:
    text = " ".join(str(record.get(k) or "") for k in ["url", "raw_url", "seed_url", "title", "github_path", "source_name", "notes"]).lower()
    for pattern, reason in LOW_VALUE_PATTERNS:
        if pattern.search(text):
            return reason
    return None


def reject(record: dict[str, Any], removed: list[dict[str, Any]], *reasons: str, extra: dict[str, Any] | None = None) -> None:
    r = deepcopy(record)
    r["filter_decision"] = "removed"
    r["filter_reasons"] = list(reasons)
    if extra:
        r.update(extra)
    removed.append(r)


def filter_records(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    removed: list[dict[str, Any]] = []
    stats = {
        "input_records": len(records),
        "step_counts": {},
        "removed_by_reason": Counter(),
        "source_caps": {},
    }

    # Step 1-3: hard filters.
    current = []
    for r in records:
        if r.get("_parse_error"):
            reject(r, removed, "invalid_json")
            continue
        if r.get("status") != "ok":
            reject(r, removed, "status_not_ok")
            continue
        content = r.get("content")
        if content is None or not str(content).strip():
            reject(r, removed, "empty_content")
            continue
        try:
            cc = int(r.get("char_count") or len(str(content)))
        except Exception:
            cc = len(str(content))
        if cc < 500:
            reject(r, removed, "char_count_lt_500", extra={"effective_char_count": cc})
            continue
        r = deepcopy(r)
        r["effective_url"] = canonical_url(r.get("url"))
        r["effective_content_hash"] = content_hash(r)
        current.append(r)
    stats["step_counts"]["after_status_content_char_count"] = len(current)

    # Step 4: URL dedup. Use the concrete crawled URL, not normalized seed URL, because GitHub repo crawls
    # often share the same normalized repo URL while individual files have unique `url` / `raw_url`.
    url_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in current:
        url_groups[r.get("effective_url") or ""].append(r)

    dedup_url = []
    for url, group in url_groups.items():
        group_sorted = sorted(group, key=score_record, reverse=True)
        keep = deepcopy(group_sorted[0])
        if len(group) > 1:
            keep["topic_tags"] = merge_topic_tags(group)
            keep["topic_count"] = len(keep["topic_tags"])
            keep["dedup_url_removed_count"] = len(group) - 1
            keep["dedup_url_merged_doc_ids"] = [g.get("doc_id") for g in group[1:] if g.get("doc_id")]
            for g in group_sorted[1:]:
                reject(g, removed, "duplicate_url", extra={"kept_doc_id": keep.get("doc_id"), "kept_url": keep.get("url")})
        dedup_url.append(keep)
    current = dedup_url
    stats["step_counts"]["after_url_dedup"] = len(current)

    # Step 5: content hash dedup.
    hash_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in current:
        hash_groups[r.get("effective_content_hash") or ""].append(r)

    dedup_hash = []
    for h, group in hash_groups.items():
        group_sorted = sorted(group, key=score_record, reverse=True)
        keep = deepcopy(group_sorted[0])
        if len(group) > 1:
            keep["dedup_content_hash_removed_count"] = len(group) - 1
            keep["dedup_content_hash_duplicate_urls"] = [g.get("url") for g in group_sorted[1:] if g.get("url")]
            keep["topic_tags"] = merge_topic_tags(group)
            keep["topic_count"] = len(keep["topic_tags"])
            for g in group_sorted[1:]:
                reject(g, removed, "duplicate_content_hash", extra={"kept_doc_id": keep.get("doc_id"), "kept_url": keep.get("url")})
        dedup_hash.append(keep)
    current = dedup_hash
    stats["step_counts"]["after_content_hash_dedup"] = len(current)

    # Step 6: low-value files.
    no_low_value = []
    for r in current:
        reason = low_value_reason(r)
        if reason:
            reject(r, removed, reason)
        else:
            no_low_value.append(r)
    current = no_low_value
    stats["step_counts"]["after_low_value_filter"] = len(current)

    # Step 7: caps/low priority for large sources.
    current_ids = set(id(r) for r in current)
    capped_out = set()
    for group_name, cfg in SOURCE_LIMITS.items():
        matched = [r for r in current if cfg["matcher"](r)]
        max_keep = int(cfg["max_keep"])
        override = cfg.get("priority_override")
        matched_sorted = sorted(matched, key=score_record, reverse=True)
        kept = matched_sorted[:max_keep]
        removed_group = matched_sorted[max_keep:]

        for r in kept:
            if override:
                r["source_priority_original"] = r.get("source_priority")
                r["source_priority"] = override
                r["filter_priority_note"] = f"Downgraded by large-source rule: {group_name}"
            r["source_limit_group"] = group_name
            r["source_limit_max_keep"] = max_keep
        for r in removed_group:
            capped_out.add(id(r))
            reject(r, removed, f"source_limit_exceeded_{group_name}", extra={"source_limit_max_keep": max_keep})

        stats["source_caps"][group_name] = {
            "matched_after_previous_filters": len(matched),
            "max_keep": max_keep,
            "kept": len(kept),
            "removed_by_cap": len(removed_group),
            "priority_override": override,
        }

    current = [r for r in current if id(r) not in capped_out]
    stats["step_counts"]["after_source_caps"] = len(current)

    # Final kept metadata.
    final = []
    for idx, r in enumerate(sorted(current, key=lambda x: (str(x.get("level_1")), str(x.get("level_2")), str(x.get("url")))), 1):
        r = deepcopy(r)
        r["filter_decision"] = "kept"
        r["filter_run_order"] = idx
        r.setdefault("crawl_enabled", True)
        final.append(r)

    # Count removal reasons.
    reason_counter = Counter()
    for r in removed:
        for reason in r.get("filter_reasons", []):
            reason_counter[reason] += 1
    stats["removed_by_reason"] = dict(reason_counter)
    stats["final_records"] = len(final)
    stats["removed_records"] = len(removed)
    stats["unique_urls_final"] = len({r.get("effective_url") or canonical_url(r.get("url")) for r in final})
    stats["unique_content_hashes_final"] = len({r.get("effective_content_hash") or content_hash(r) for r in final})
    stats["final_by_level_1"] = dict(Counter(r.get("level_1") for r in final))
    stats["final_by_source_priority"] = dict(Counter(r.get("source_priority") for r in final))
    stats["final_by_crawl_type"] = dict(Counter(r.get("crawl_type") for r in final))
    stats["final_by_source_type"] = dict(Counter(r.get("source_type") for r in final))
    return final, removed, stats


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def write_report(path: Path, stats: dict[str, Any]) -> None:
    lines = []
    lines.append("# records.jsonl 筛选报告")
    lines.append("")
    lines.append(f"- 输入记录数：{stats['input_records']}")
    for k, v in stats["step_counts"].items():
        lines.append(f"- {k}：{v}")
    lines.append(f"- 最终保留记录数：{stats['final_records']}")
    lines.append(f"- 剔除记录数：{stats['removed_records']}")
    lines.append(f"- 最终唯一 URL 数：{stats['unique_urls_final']}")
    lines.append(f"- 最终唯一 content_hash 数：{stats['unique_content_hashes_final']}")
    lines.append("")
    lines.append("## 剔除原因统计")
    for reason, count in sorted(stats["removed_by_reason"].items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"- {reason}: {count}")
    lines.append("")
    lines.append("## 大体量来源限额/降权")
    for group, info in stats["source_caps"].items():
        lines.append(f"- {group}: matched={info['matched_after_previous_filters']}, max_keep={info['max_keep']}, kept={info['kept']}, removed_by_cap={info['removed_by_cap']}, priority_override={info['priority_override']}")
    lines.append("")
    lines.append("## 最终记录分布")
    lines.append("### level_1")
    for k, v in sorted(stats["final_by_level_1"].items(), key=lambda kv: str(kv[0])):
        lines.append(f"- {k}: {v}")
    lines.append("### source_priority")
    for k, v in sorted(stats["final_by_source_priority"].items(), key=lambda kv: str(kv[0])):
        lines.append(f"- {k}: {v}")
    lines.append("### crawl_type")
    for k, v in sorted(stats["final_by_crawl_type"].items(), key=lambda kv: str(kv[0])):
        lines.append(f"- {k}: {v}")
    lines.append("### source_type")
    for k, v in sorted(stats["final_by_source_type"].items(), key=lambda kv: str(kv[0])):
        lines.append(f"- {k}: {v}")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("input", type=Path)
    ap.add_argument("--out-dir", type=Path, default=Path("."))
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    records = load_jsonl(args.input)
    final, removed, stats = filter_records(records)

    write_jsonl(args.out_dir / "records_filtered.jsonl", final)
    write_jsonl(args.out_dir / "records_removed.jsonl", removed)
    (args.out_dir / "filter_summary.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(args.out_dir / "filter_report.md", stats)

    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
