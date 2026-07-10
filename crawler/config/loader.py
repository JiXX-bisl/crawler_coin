# -*- coding: utf-8 -*-
import json
from pathlib import Path
from typing import Any, Dict, List

from crawler.core.models import (
    CrawlConfig,
    DiscoveryConfig,
    ExtractionConfig,
    OutputConfig,
    RuntimeConfig,
    SourceConfig,
    UrlRules,
)


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def load_config(path, output_dir=None) -> CrawlConfig:
    raw = load_json(path)
    if isinstance(raw, dict) and "job" in raw and "sources" in raw:
        config = from_unified(raw)
    elif isinstance(raw, dict) and raw.get("config_name") == "virtual_currency_illegal_case_sources":
        config = from_illegal_case_legacy(raw)
    elif isinstance(raw, list):
        config = from_knowledge_legacy(raw)
    elif isinstance(raw, dict) and "sources" in raw:
        config = from_illegal_case_legacy(raw)
    else:
        raise ValueError("Unsupported crawl config format: %s" % path)
    if output_dir:
        config.output.directory = output_dir
    return config


def runtime_from_dict(data: Dict[str, Any]) -> RuntimeConfig:
    dedup = data.get("deduplication") or {}
    return RuntimeConfig(
        request_interval_seconds=float(data.get("request_interval_seconds", data.get("request_interval", 1.0))),
        timeout_seconds=int(data.get("timeout_seconds", data.get("timeout", 30))),
        max_retries=int(data.get("max_retries", 2)),
        respect_robots_txt=bool(data.get("respect_robots_txt", True)),
        user_agent=data.get("user_agent") or RuntimeConfig.user_agent,
        deduplicate_url=bool(dedup.get("url", data.get("deduplicate_by_normalized_url", True))),
        deduplicate_content=bool(dedup.get("content", True)),
    )


def output_from_dict(data: Dict[str, Any]) -> OutputConfig:
    return OutputConfig(
        directory=data.get("directory", "data/unified_crawl"),
        records_file=data.get("records_file", "records.jsonl"),
        failures_file=data.get("failures_file", "failures.jsonl"),
        report_file=data.get("report_file", "crawl_report.json"),
    )


def source_from_unified(data: Dict[str, Any], defaults: Dict[str, Any]) -> SourceConfig:
    runtime_crawl = defaults.get("crawl") or {}
    seed = data.get("seed") or {}
    crawl = data.get("crawl") or {}
    url_rules = data.get("url_rules") or {}
    discovery = data.get("discovery") or {}
    extraction = data.get("extraction") or {}
    start_urls = data.get("start_urls") or as_list(seed.get("url"))
    return SourceConfig(
        id=str(data["id"]),
        enabled=bool(data.get("enabled", True)),
        start_urls=[str(u) for u in start_urls if u],
        fetcher=data.get("fetcher") or seed.get("fetcher") or "http",
        parser=data.get("parser") or seed.get("parser") or seed.get("crawl_type") or "html",
        discoverer=data.get("discoverer") or discovery.get("discoverer") or ("html_links" if discovery.get("enabled") else "none"),
        extractor=data.get("extractor") or extraction.get("strategy") or "generic_article",
        pipeline=data.get("pipeline", "default"),
        max_depth=int(crawl.get("max_depth", runtime_crawl.get("max_depth", 0))),
        max_pages=int(crawl.get("max_pages", runtime_crawl.get("max_pages", 1))),
        requires_javascript=bool(crawl.get("requires_javascript", False)),
        url_rules=UrlRules(
            same_domain=bool(crawl.get("same_domain", url_rules.get("same_domain", True))),
            allow_patterns=as_list(url_rules.get("allow_patterns")),
            deny_patterns=as_list(url_rules.get("deny_patterns")),
            path_prefix=url_rules.get("path_prefix"),
        ),
        discovery=DiscoveryConfig(
            enabled=bool(discovery.get("enabled", False)),
            positive_keywords=as_list(discovery.get("positive_keywords")),
            negative_keywords=as_list(discovery.get("negative_keywords")),
            min_link_score=float(discovery.get("min_link_score", 0.0)),
        ),
        extraction=ExtractionConfig(
            strategy=extraction.get("strategy", "generic_article"),
            fields=extraction.get("fields") or {},
            fallback=extraction.get("fallback", "generic_article"),
        ),
        filters=as_list(data.get("filters")),
        metadata=data.get("metadata") or {},
    )


def from_unified(raw: Dict[str, Any]) -> CrawlConfig:
    defaults = raw.get("defaults") or {}
    runtime = runtime_from_dict(raw.get("runtime") or defaults.get("request") or {})
    output = output_from_dict(raw.get("output") or {})
    return CrawlConfig(
        job=raw.get("job") or {},
        runtime=runtime,
        output=output,
        sources=[source_from_unified(s, defaults) for s in raw.get("sources", [])],
    )


def from_illegal_case_legacy(raw: Dict[str, Any]) -> CrawlConfig:
    policy = raw.get("global_crawl_policy") or {}
    runtime = runtime_from_dict(policy)
    sources: List[SourceConfig] = []
    for item in raw.get("sources", []):
        source_id = str(item.get("source_id") or item.get("id"))
        seed_url = item.get("seed_url") or item.get("url")
        metadata = {"illegal_case": dict(item)}
        keywords = as_list(item.get("search_keywords"))
        sources.append(
            SourceConfig(
                id=source_id,
                enabled=bool(item.get("enabled", True)),
                start_urls=[seed_url] if seed_url else [],
                fetcher="http",
                parser="auto",
                discoverer="html_links" if item.get("contains_case_list") else "none",
                extractor="generic_article",
                pipeline="legacy_illegal_case",
                max_depth=int(item.get("crawl_depth", policy.get("max_default_crawl_depth", 1))),
                max_pages=int(item.get("max_pages_per_seed", policy.get("max_pages_per_seed", 1))),
                url_rules=UrlRules(
                    same_domain=not bool(item.get("follow_external_links", policy.get("follow_external_links", False))),
                    allow_patterns=as_list(item.get("allow_url_patterns")),
                    deny_patterns=as_list(item.get("deny_url_patterns")),
                ),
                discovery=DiscoveryConfig(enabled=True, positive_keywords=keywords, min_link_score=0.1),
                metadata=metadata,
            )
        )
    return CrawlConfig(
        job={"id": raw.get("config_name", "legacy_illegal_case"), "metadata": {"legacy_format": "illegal_case"}},
        runtime=runtime,
        output=OutputConfig(directory="data/unified_illegal_case_crawl"),
        sources=sources,
    )


def from_knowledge_legacy(items: List[Dict[str, Any]]) -> CrawlConfig:
    runtime = RuntimeConfig(request_interval_seconds=1.0, timeout_seconds=60, max_retries=2)
    sources = []
    for item in items:
        url = item.get("normalized_url") or item.get("url") or item.get("seed_url")
        crawl_type = item.get("crawl_type") or "html"
        discoverer = "html_links" if crawl_type == "html_crawl" else "none"
        sources.append(
            SourceConfig(
                id=str(item.get("id")),
                enabled=bool(item.get("crawl_enabled", True)),
                start_urls=[url] if url else [],
                fetcher="http",
                parser="pdf" if crawl_type == "pdf" else "auto",
                discoverer=discoverer,
                extractor="generic_article",
                pipeline="legacy_knowledge",
                max_depth=int(item.get("max_depth", 1 if discoverer != "none" else 0)),
                max_pages=int(item.get("max_pages", 50 if discoverer != "none" else 1)),
                url_rules=UrlRules(same_domain=True, allow_patterns=as_list(item.get("allow_url_patterns")), deny_patterns=as_list(item.get("deny_url_patterns"))),
                discovery=DiscoveryConfig(enabled=discoverer != "none", positive_keywords=as_list(item.get("topic_tags")), min_link_score=0.0),
                metadata={"knowledge": dict(item)},
            )
        )
    return CrawlConfig(
        job={"id": "legacy_knowledge", "metadata": {"legacy_format": "knowledge"}},
        runtime=runtime,
        output=OutputConfig(directory="data/unified_knowledge_crawl"),
        sources=sources,
    )
