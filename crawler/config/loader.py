# -*- coding: utf-8 -*-
import json
from pathlib import Path
from typing import Any, Dict

from crawler.core.models import CrawlConfig, DiscoveryConfig, ExtractionConfig, OutputConfig, RuntimeConfig, SourceConfig, UrlRules


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def load_config(path, output_dir=None) -> CrawlConfig:
    raw = load_json(path)
    if not isinstance(raw, dict) or "job" not in raw or "sources" not in raw:
        raise ValueError("Config must use unified_web_crawl_config format: %s" % path)
    config = from_unified(raw)
    if output_dir:
        config.output.directory = output_dir
    return config


def runtime_from_dict(data: Dict[str, Any]) -> RuntimeConfig:
    dedup = data.get("deduplication") or {}
    expansion_mode = str(data.get("expansion_mode", "controlled")).lower()
    if expansion_mode not in {"strict", "controlled"}:
        raise ValueError("runtime.expansion_mode must be strict or controlled")
    return RuntimeConfig(
        request_interval_seconds=float(data.get("request_interval_seconds", data.get("request_interval", 1.0))),
        timeout_seconds=int(data.get("timeout_seconds", data.get("timeout", 30))),
        max_retries=int(data.get("max_retries", 2)),
        respect_robots_txt=bool(data.get("respect_robots_txt", True)),
        user_agent=data.get("user_agent") or RuntimeConfig.user_agent,
        deduplicate_url=bool(dedup.get("url", data.get("deduplicate_by_normalized_url", True))),
        deduplicate_content=bool(dedup.get("content", True)),
        expansion_mode=expansion_mode,
    )


def output_from_dict(data: Dict[str, Any]) -> OutputConfig:
    return OutputConfig(
        directory=data.get("directory", "data/unified_crawl"),
        records_file=data.get("records_file", "records.jsonl"),
        failures_file=data.get("failures_file", "failures.jsonl"),
        report_file=data.get("report_file", "crawl_report.json"),
        expansion_report_file=data.get("expansion_report_file", "expansion_report.json"),
        associations_file=data.get("associations_file", "source_associations.jsonl"),
        failure_domain_report_file=data.get("failure_domain_report_file", "failure_domain_report.json"),
    )


def source_from_unified(data: Dict[str, Any], defaults: Dict[str, Any]) -> SourceConfig:
    runtime_crawl = defaults.get("crawl") or {}
    seed = data.get("seed") or {}
    crawl = data.get("crawl") or {}
    url_rules = data.get("url_rules") or {}
    discovery = data.get("discovery") or {}
    extraction = data.get("extraction") or {}
    metadata = data.get("metadata") or {}
    case_metadata = metadata.get("illegal_case") or {}
    start_urls = data.get("start_urls") or as_list(seed.get("url"))
    source_role = data.get("source_role") or case_metadata.get("entry_type") or "case_detail"
    return SourceConfig(
        id=str(data["id"]),
        enabled=bool(data.get("enabled", True)),
        start_urls=[str(url) for url in start_urls if url],
        fetcher=data.get("fetcher") or seed.get("fetcher") or "http",
        parser=data.get("parser") or seed.get("parser") or seed.get("crawl_type") or "html",
        discoverer=data.get("discoverer") or discovery.get("discoverer") or ("html_links" if discovery.get("enabled") else "none"),
        extractor=data.get("extractor") or extraction.get("strategy") or "generic_article",
        pipeline=data.get("pipeline", "default"),
        source_role=str(source_role),
        max_depth=int(crawl.get("max_depth", runtime_crawl.get("max_depth", 0))),
        max_pages=int(crawl.get("max_pages", runtime_crawl.get("max_pages", 1))),
        requires_javascript=bool(crawl.get("requires_javascript", False)),
        request_options=data.get("request") or {},
        url_rules=UrlRules(
            same_domain=bool(crawl.get("same_domain", url_rules.get("same_domain", True))),
            allow_patterns=as_list(url_rules.get("allow_patterns")),
            deny_patterns=as_list(url_rules.get("deny_patterns")),
            path_prefix=url_rules.get("path_prefix"),
            allowed_domains=[str(domain).lower() for domain in as_list(url_rules.get("allowed_domains")) if domain],
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
        metadata=metadata,
    )


def from_unified(raw: Dict[str, Any]) -> CrawlConfig:
    defaults = raw.get("defaults") or {}
    return CrawlConfig(
        job=raw.get("job") or {},
        runtime=runtime_from_dict(raw.get("runtime") or defaults.get("request") or {}),
        output=output_from_dict(raw.get("output") or {}),
        sources=[source_from_unified(source, defaults) for source in raw.get("sources", [])],
    )
