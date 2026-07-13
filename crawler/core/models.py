# -*- coding: utf-8 -*-
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class RuntimeConfig:
    request_interval_seconds: float = 1.0
    timeout_seconds: int = 30
    max_retries: int = 2
    respect_robots_txt: bool = True
    user_agent: str = "UnifiedCrawler/1.0"
    deduplicate_url: bool = True
    deduplicate_content: bool = True
    expansion_mode: str = "controlled"


@dataclass
class OutputConfig:
    directory: str = "data/unified_crawl"
    records_file: str = "records.jsonl"
    failures_file: str = "failures.jsonl"
    report_file: str = "crawl_report.json"
    expansion_report_file: str = "expansion_report.json"
    associations_file: str = "source_associations.jsonl"
    failure_domain_report_file: str = "failure_domain_report.json"


@dataclass
class UrlRules:
    same_domain: bool = True
    allow_patterns: List[str] = field(default_factory=list)
    deny_patterns: List[str] = field(default_factory=list)
    path_prefix: Optional[str] = None
    allowed_domains: List[str] = field(default_factory=list)


@dataclass
class DiscoveryConfig:
    enabled: bool = False
    positive_keywords: List[str] = field(default_factory=list)
    negative_keywords: List[str] = field(default_factory=list)
    min_link_score: float = 0.0


@dataclass
class ExtractionConfig:
    strategy: str = "generic_article"
    fields: Dict[str, Any] = field(default_factory=dict)
    fallback: str = "generic_article"


@dataclass
class SourceConfig:
    id: str
    enabled: bool
    start_urls: List[str]
    fetcher: str = "http"
    parser: str = "html"
    discoverer: str = "none"
    extractor: str = "generic_article"
    pipeline: str = "default"
    source_role: str = "case_detail"
    max_depth: int = 0
    max_pages: int = 1
    requires_javascript: bool = False
    request_options: Dict[str, Any] = field(default_factory=dict)
    url_rules: UrlRules = field(default_factory=UrlRules)
    discovery: DiscoveryConfig = field(default_factory=DiscoveryConfig)
    extraction: ExtractionConfig = field(default_factory=ExtractionConfig)
    filters: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CrawlConfig:
    job: Dict[str, Any]
    runtime: RuntimeConfig
    output: OutputConfig
    sources: List[SourceConfig]


@dataclass
class QueueItem:
    source_id: str
    url: str
    depth: int = 0
    parent_url: Optional[str] = None
    context: str = ""
    anchor_text: str = ""
    node_type: str = "html"
