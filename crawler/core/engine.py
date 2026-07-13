# -*- coding: utf-8 -*-
import hashlib
import json
import time
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from urllib import robotparser
from urllib.parse import urlparse

from crawler.components.case_rules import extract_case_facts
from crawler.components.discovery import discover_html_links, link_has_negative_signal, link_score
from crawler.components.exporters import JsonlExporter
from crawler.components.extractors import extract_html, extract_pdf
from crawler.components.fetchers import HttpFetcher
from crawler.components.parsers import detect_parser, parse_html, parse_pdf
from crawler.components.urls import allowed_by_rules, classify_url, normalize_url
from crawler.core.models import QueueItem

DEFAULT_TOPIC_KEYWORDS = {
    "illegal_cases": [
        "\u865a\u62df\u8d27\u5e01", "\u6570\u5b57\u8d27\u5e01", "\u6bd4\u7279\u5e01", "usdt", "\u6cf0\u8fbe\u5e01", "\u533a\u5757\u94fe", "nft", "\u6570\u5b57\u85cf\u54c1",
        "\u6d17\u94b1", "\u8bc8\u9a97", "\u4f20\u9500", "\u975e\u6cd5\u7ecf\u8425", "\u8d4c\u535a", "\u5e2e\u4fe1", "\u6392\u9970\u9690\u7792", "\u6cd5\u9662", "\u68c0\u5bdf\u9662", "\u516c\u5b89", "\u5224\u51b3", "\u5178\u578b\u6848\u4f8b",
    ],
    "coin_knowledge": [
        "bitcoin", "crypto", "cryptocurrency", "blockchain", "wallet", "transaction", "address",
        "contract", "protocol", "security", "compliance", "guide", "documentation", "developer",
        "\u6bd4\u7279\u5e01", "\u533a\u5757\u94fe", "\u94b1\u5305", "\u4ea4\u6613", "\u5730\u5740", "\u5408\u7ea6", "\u534f\u8bae", "\u5b89\u5168", "\u5408\u89c4", "\u6587\u6863",
    ],
}
CASE_EXPANDABLE_ROLES = {"topic_page", "case_list", "announcement_list"}


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_text(text):
    return hashlib.sha256((text or "").encode("utf-8", errors="ignore")).hexdigest()


class RobotGuard:
    def __init__(self, enabled, user_agent):
        self.enabled = enabled
        self.user_agent = user_agent
        self.cache = {}

    def allowed(self, url):
        if not self.enabled:
            return True, "robots_disabled"
        parsed = normalize_url(url)
        if not parsed:
            return False, "invalid_url"
        info = urlparse(parsed)
        base = "%s://%s" % (info.scheme, info.netloc)
        if base not in self.cache:
            parser = robotparser.RobotFileParser()
            parser.set_url(base + "/robots.txt")
            try:
                parser.read()
                self.cache[base] = parser
            except Exception:
                self.cache[base] = None
        parser = self.cache[base]
        if parser is None:
            return True, "robots_fetch_failed"
        allowed = parser.can_fetch(self.user_agent, parsed)
        return allowed, "robots_allowed" if allowed else "robots_denied"


class CrawlEngine:
    def __init__(self, config, progress=True):
        self.config = config
        self.progress = progress
        self.url_seen = set()
        self.content_seen = set()
        self.record_by_url = {}
        self.record_by_content_hash = {}
        self.stats = Counter()
        self.last_request_at = {}
        self.expansion_reports = []
        self.failure_domains = defaultdict(Counter)

    def log(self, message):
        if self.progress:
            print("[%s] %s" % (now_iso(), message), flush=True)

    @property
    def is_case_job(self):
        return self.config.job.get("id") == "illegal_cases"

    def run(self):
        exporter = JsonlExporter(
            self.config.output.directory,
            self.config.output.records_file,
            self.config.output.failures_file,
            self.config.output.associations_file,
        )
        try:
            self.log("start job=%s sources=%s output=%s expansion_mode=%s" % (
                self.config.job.get("id"), len(self.config.sources), self.config.output.directory, self.config.runtime.expansion_mode
            ))
            for source in self.config.sources:
                if source.enabled:
                    self.run_source(source, exporter)
            report = {
                "job": self.config.job,
                "stats": dict(self.stats),
                "expansion_mode": self.config.runtime.expansion_mode,
                "expansion_sources": len(self.expansion_reports),
                "finished_at": now_iso(),
            }
            out_dir = Path(self.config.output.directory)
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / self.config.output.report_file).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            (out_dir / self.config.output.expansion_report_file).write_text(
                json.dumps({"job": self.config.job, "sources": self.expansion_reports}, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (out_dir / self.config.output.failure_domain_report_file).write_text(
                json.dumps({"job": self.config.job, "domains": self.failure_domain_summary()}, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self.log("done records=%s failures=%s associations=%s" % (
                self.stats["records"], self.stats["failures"], self.stats["associations"]
            ))
            return report
        finally:
            exporter.close()

    def run_source(self, source, exporter):
        options = source.request_options or {}
        fetcher = HttpFetcher(
            self.config.runtime.user_agent,
            self.config.runtime.timeout_seconds,
            self.config.runtime.max_retries,
            verify_tls=bool(options.get("verify_tls", True)),
            trust_env=bool(options.get("trust_env", True)),
            proxies=options.get("proxies") or None,
        )
        robots = RobotGuard(self.config.runtime.respect_robots_txt, self.config.runtime.user_agent)
        seed_url = normalize_url(source.start_urls[0]) if source.start_urls else ""
        queue = deque()
        queued_urls = set()
        for start_url in source.start_urls:
            normalized = normalize_url(start_url)
            if normalized and normalized not in queued_urls:
                queue.append(QueueItem(source.id, normalized, 0, None, "", "", self.source_node_type(source, normalized)))
                queued_urls.add(normalized)

        expansion = self.new_expansion_report(source, queue)
        attempted = 0
        self.log("source=%s role=%s start max_pages=%s max_depth=%s" % (
            source.id, source.source_role, source.max_pages, source.max_depth
        ))
        while queue and attempted < source.max_pages:
            item = queue.popleft()
            url = normalize_url(item.url)
            if not url:
                self.note_filtered(expansion, "invalid_url")
                self.failure(exporter, source, item, "invalid_url")
                continue
            if self.config.runtime.deduplicate_url and url in self.url_seen:
                self.stats["skipped"] += 1
                self.note_filtered(expansion, "duplicate_url")
                self.write_duplicate_url_association(exporter, source, item, url)
                continue
            allowed, rule_reason = self.url_allowed(url, seed_url, source, item.node_type)
            if not allowed:
                self.stats["filtered_urls"] += 1
                self.note_filtered(expansion, rule_reason)
                continue
            robots_allowed, robots_reason = robots.allowed(url)
            if not robots_allowed:
                self.note_filtered(expansion, "robots_denied")
                self.failure(exporter, source, item, "robots_denied", extra={"robots": robots_reason})
                continue
            self.wait_for_domain(url)
            attempted += 1
            expansion["fetched"] += 1
            self.url_seen.add(url)
            self.log("fetch source=%s %s/%s depth=%s type=%s url=%s" % (
                source.id, attempted, source.max_pages, item.depth, item.node_type, url
            ))
            result = fetcher.fetch(url)
            if not result.get("ok"):
                self.failure(exporter, source, item, result.get("error_type") or "fetch_failed", result)
                continue

            final_url = normalize_url(result.get("final_url") or url) or url
            final_allowed, final_reason = self.url_allowed(final_url, seed_url, source, item.node_type)
            if not final_allowed:
                self.note_filtered(expansion, "redirect_" + final_reason)
                self.failure(exporter, source, item, "redirect_out_of_scope", result, {"final_url": final_url, "reason": final_reason})
                continue

            record, parsed = self.build_record(source, item, url, result)
            if record["node_type"] == "html":
                expansion["max_html_depth_reached"] = max(expansion["max_html_depth_reached"], item.depth)
            else:
                expansion["terminal_content_fetched"] += 1
            if not self.keep_record(record, source):
                self.stats["filtered_records"] += 1
            else:
                exporter.write_record(record)
                self.stats["records"] += 1
                self.record_by_url[record["normalized_url"]] = record
                if record.get("content_hash") and not record.get("duplicate_content"):
                    self.record_by_content_hash[record["content_hash"]] = record
            if record["duplicate_content"]:
                self.note_filtered(expansion, "duplicate_content")
                self.write_duplicate_content_association(exporter, source, item, record)
                continue
            if self.can_discover(source, item, parsed):
                self.expand_html_links(source, item, final_url, parsed, queue, queued_urls, attempted, expansion)

        if queue and attempted >= source.max_pages:
            self.note_filtered(expansion, "max_pages_reached")
        if expansion["candidates_discovered"] and not expansion["candidates_accepted"]:
            self.note_unable(expansion, "all_candidates_filtered")
        self.expansion_reports.append(expansion)
        self.log("source=%s done attempted=%s records=%s failures=%s" % (
            source.id, attempted, self.stats["records"], self.stats["failures"]
        ))

    def new_expansion_report(self, source, queue):
        source_type = queue[0].node_type if queue else "unknown"
        report = {
            "source_id": source.id,
            "source_role": source.source_role,
            "expansion_mode": self.config.runtime.expansion_mode,
            "source_node_type": source_type,
            "max_depth": source.max_depth,
            "max_pages": source.max_pages,
            "discoverer": source.discoverer,
            "candidates_discovered": 0,
            "eligible_candidates": 0,
            "candidates_accepted": 0,
            "html_candidates_accepted": 0,
            "terminal_candidates_accepted": 0,
            "terminal_content_fetched": 0,
            "fetched": 0,
            "max_html_depth_reached": 0,
            "accepted_link_types": {},
            "accepted_path_prefixes": {},
            "filtered_reasons": {},
            "unable_reasons": [],
        }
        if source_type != "html":
            self.note_unable(report, "non_html_seed")
        elif source.discoverer != "html_links":
            self.note_unable(report, "discoverer_disabled")
        elif self.is_case_job and source.source_role not in CASE_EXPANDABLE_ROLES:
            self.note_unable(report, "source_role_not_expandable")
        elif source.max_depth <= 0:
            self.note_unable(report, "max_depth_zero")
        elif source.max_pages <= 1:
            self.note_unable(report, "max_pages_one")
        return report

    def source_node_type(self, source, url):
        parser = (source.parser or "").lower()
        if parser == "pdf":
            return "pdf"
        if parser in {"github", "github_markdown", "markdown"}:
            return "github"
        return classify_url(url)

    def url_allowed(self, url, seed_url, source, node_type):
        allowed, reason = allowed_by_rules(url, seed_url, source.url_rules)
        if allowed:
            return True, reason
        if self.config.runtime.expansion_mode == "controlled" and node_type == "html" and reason == "allow_regex_miss":
            return True, "controlled_allow_regex_miss"
        return False, reason

    def can_discover(self, source, item, parsed):
        return bool(
            item.node_type == "html"
            and source.discoverer == "html_links"
            and source.max_depth > 0
            and source.max_pages > 1
            and (not self.is_case_job or source.source_role in CASE_EXPANDABLE_ROLES)
            and parsed
            and parsed.get("parser") == "html"
        )

    def topic_keywords(self, source):
        values = list(source.discovery.positive_keywords)
        metadata = source.metadata or {}
        for key in ("knowledge", "illegal_case"):
            section = metadata.get(key) or {}
            for field in ("link_score_keywords", "search_keywords", "topic_tags", "crime_types", "content_focus", "knowledge_category", "coin", "chain"):
                value = section.get(field)
                if isinstance(value, list):
                    values.extend(str(item) for item in value if item)
                elif value:
                    values.append(str(value))
        values.extend(DEFAULT_TOPIC_KEYWORDS.get(str(self.config.job.get("id")), []))
        return list(dict.fromkeys(value.lower() for value in values if value))

    def case_link_allowed(self, link):
        if link["node_type"] != "html":
            return True, "case_terminal_content"
        if link.get("is_pagination"):
            return True, "case_pagination"
        if link.get("is_case_detail"):
            return True, "case_detail_signal"
        return False, "case_detail_signal_missing"

    def expand_html_links(self, source, item, base_url, parsed, queue, queued_urls, attempted, expansion):
        candidates = []
        keywords = self.topic_keywords(source)
        for link in discover_html_links(parsed, base_url):
            expansion["candidates_discovered"] += 1
            url = link["url"]
            node_type = link["node_type"]
            if node_type == "unsupported":
                self.note_filtered(expansion, "unsupported_resource_type")
                continue
            if self.config.runtime.deduplicate_url and (url in self.url_seen or url in queued_urls):
                self.note_filtered(expansion, "duplicate_url")
                continue
            if link_has_negative_signal(link, source.discovery.negative_keywords):
                self.note_filtered(expansion, "negative_topic_signal")
                continue
            if node_type == "html" and item.depth >= source.max_depth:
                self.note_filtered(expansion, "max_depth_reached")
                continue
            allowed, reason = self.url_allowed(url, source.start_urls[0], source, node_type)
            if not allowed:
                self.note_filtered(expansion, reason)
                continue
            if self.is_case_job:
                case_allowed, case_reason = self.case_link_allowed(link)
                if not case_allowed:
                    self.note_filtered(expansion, case_reason)
                    continue
            score = link_score(link, source.discovery, keywords, multi_signal=self.is_case_job)
            if node_type == "html" and self.config.runtime.expansion_mode == "controlled" and score < source.discovery.min_link_score:
                self.note_filtered(expansion, "topic_not_relevant")
                continue
            candidates.append((score, link))
            expansion["eligible_candidates"] += 1

        candidates.sort(key=lambda candidate: candidate[0], reverse=True)
        capacity = max(0, source.max_pages - attempted - len(queue))
        for score, link in candidates:
            if capacity <= 0:
                self.note_filtered(expansion, "max_pages_reached")
                continue
            node_type = link["node_type"]
            depth = item.depth + 1 if node_type == "html" else item.depth
            queue.append(QueueItem(source.id, link["url"], depth, base_url, link.get("context", ""), link.get("anchor_text", ""), node_type))
            queued_urls.add(link["url"])
            capacity -= 1
            expansion["candidates_accepted"] += 1
            if node_type == "html":
                expansion["html_candidates_accepted"] += 1
            else:
                expansion["terminal_candidates_accepted"] += 1
            self.note_accepted_pattern(expansion, link)

    def note_accepted_pattern(self, expansion, link):
        type_counts = Counter(expansion["accepted_link_types"])
        type_counts[link["node_type"]] += 1
        expansion["accepted_link_types"] = dict(type_counts)
        parts = [part for part in urlparse(link["url"]).path.split("/") if part]
        prefix = "/" + "/".join(parts[:2]) if parts else "/"
        prefix_counts = Counter(expansion["accepted_path_prefixes"])
        prefix_counts[prefix] += 1
        expansion["accepted_path_prefixes"] = dict(prefix_counts)

    @staticmethod
    def note_filtered(expansion, reason):
        counts = Counter(expansion["filtered_reasons"])
        counts[reason] += 1
        expansion["filtered_reasons"] = dict(counts)

    @staticmethod
    def note_unable(expansion, reason):
        if reason not in expansion["unable_reasons"]:
            expansion["unable_reasons"].append(reason)

    def wait_for_domain(self, url):
        domain = urlparse(url).netloc
        elapsed = time.time() - self.last_request_at.get(domain, 0)
        wait = self.config.runtime.request_interval_seconds - elapsed
        if wait > 0:
            time.sleep(wait)
        self.last_request_at[domain] = time.time()

    def build_record(self, source, item, url, fetch_result):
        parser_name = detect_parser(fetch_result.get("final_url") or url, fetch_result.get("headers") or {}, source.parser)
        if parser_name == "pdf":
            parsed = parse_pdf(fetch_result.get("body") or b"")
            extracted = extract_pdf(parsed)
            node_type = "pdf"
        else:
            parsed = parse_html(fetch_result.get("body") or b"", fetch_result.get("headers") or {})
            extracted = extract_html(parsed, source.extraction)
            node_type = item.node_type
        content = extracted.get("content") or ""
        content_hash = sha256_text(content) if content else None
        duplicate_content = bool(content_hash and self.config.runtime.deduplicate_content and content_hash in self.content_seen)
        if content_hash:
            self.content_seen.add(content_hash)
        record = {
            "doc_id": "%s_%s" % (source.id, sha256_text(url)[:16]),
            "source_id": source.id,
            "job_id": self.config.job.get("id"),
            "seed_url": source.start_urls[0] if source.start_urls else None,
            "url": url,
            "final_url": fetch_result.get("final_url"),
            "normalized_url": normalize_url(fetch_result.get("final_url") or url),
            "title": extracted.get("title"),
            "content": content,
            "content_hash": content_hash,
            "char_count": len(content),
            "fetcher": source.fetcher,
            "parser": parser_name,
            "extractor": source.extractor,
            "node_type": node_type,
            "source_role": source.source_role,
            "crawl_depth": item.depth,
            "parent_url": item.parent_url,
            "link_anchor_text": item.anchor_text or None,
            "link_context": item.context or None,
            "status": "ok",
            "http_status": fetch_result.get("status_code"),
            "crawl_time": now_iso(),
            "duplicate_content": duplicate_content,
            "metadata": source.metadata,
            "content_blocks": extracted.get("content_blocks") or [],
            "pdf_pages": extracted.get("pdf_pages") or [],
            "case_facts": extract_case_facts(content) if self.is_case_job and parser_name == "html" else None,
        }
        return record, parsed

    def keep_record(self, record, source):
        for rule in source.filters:
            if not isinstance(rule, dict):
                continue
            field = rule.get("field", "content")
            if rule.get("type") == "minimum_length" and record.get(field):
                if len(record.get(field) or "") < int(rule.get("value", 0)):
                    return False
            if rule.get("type") == "keyword_any":
                text = record.get(field) or ""
                if not any(str(value) in text for value in rule.get("values", [])):
                    return False
        return True

    def write_duplicate_url_association(self, exporter, source, item, url):
        canonical = self.record_by_url.get(url)
        if not canonical:
            return
        exporter.write_association(self.association_record(canonical, source, item, url, "duplicate_url"))
        self.stats["associations"] += 1

    def write_duplicate_content_association(self, exporter, source, item, record):
        canonical = self.record_by_content_hash.get(record.get("content_hash"))
        if not canonical:
            return
        exporter.write_association(self.association_record(canonical, source, item, record["normalized_url"], "duplicate_content"))
        self.stats["associations"] += 1

    @staticmethod
    def association_record(canonical, source, item, observed_url, relation):
        return {
            "relation": relation,
            "canonical_doc_id": canonical.get("doc_id"),
            "canonical_url": canonical.get("normalized_url"),
            "observed_url": observed_url,
            "source_id": source.id,
            "source_role": source.source_role,
            "source_metadata": source.metadata,
            "parent_url": item.parent_url,
            "crawl_depth": item.depth,
            "time": now_iso(),
        }

    def failure(self, exporter, source, item, error_type, result=None, extra=None):
        failure = {
            "source_id": source.id,
            "url": item.url,
            "depth": item.depth,
            "node_type": item.node_type,
            "error_type": error_type,
            "time": now_iso(),
        }
        if isinstance(result, dict):
            failure.update({key: result.get(key) for key in ("status_code", "retry_count", "exception") if result.get(key) is not None})
        if extra:
            failure.update(extra)
        exporter.write_failure(failure)
        self.stats["failures"] += 1
        domain = (urlparse(item.url).hostname or "").lower()
        if domain:
            self.failure_domains[domain]["failures"] += 1
            self.failure_domains[domain]["error_type:" + error_type] += 1
            if failure.get("status_code") is not None:
                self.failure_domains[domain]["status:" + str(failure["status_code"])] += 1

    def failure_domain_summary(self):
        summary = []
        for domain, counts in sorted(self.failure_domains.items()):
            error_types = {key.split(":", 1)[1]: value for key, value in counts.items() if key.startswith("error_type:")}
            status_codes = {key.split(":", 1)[1]: value for key, value in counts.items() if key.startswith("status:")}
            summary.append({"domain": domain, "failures": counts["failures"], "error_types": error_types, "status_codes": status_codes})
        return summary
