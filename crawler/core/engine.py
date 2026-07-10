# -*- coding: utf-8 -*-
import hashlib
import json
import time
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path
from urllib import robotparser

from crawler.components.discovery import discover_html_links, link_score
from crawler.components.exporters import JsonlExporter
from crawler.components.extractors import extract_html, extract_pdf
from crawler.components.fetchers import HttpFetcher
from crawler.components.parsers import detect_parser, parse_html, parse_pdf
from crawler.components.urls import allowed_by_rules, normalize_url
from crawler.core.models import QueueItem


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
        from urllib.parse import urlparse

        info = urlparse(parsed)
        base = "%s://%s" % (info.scheme, info.netloc)
        if base not in self.cache:
            rp = robotparser.RobotFileParser()
            rp.set_url(base + "/robots.txt")
            try:
                rp.read()
                self.cache[base] = rp
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
        self.stats = Counter()
        self.last_request_at = {}

    def log(self, message):
        if self.progress:
            print("[%s] %s" % (now_iso(), message), flush=True)

    def run(self):
        exporter = JsonlExporter(self.config.output.directory, self.config.output.records_file, self.config.output.failures_file)
        try:
            self.log("start job=%s sources=%s output=%s" % (self.config.job.get("id"), len(self.config.sources), self.config.output.directory))
            for source in self.config.sources:
                if not source.enabled:
                    continue
                self.run_source(source, exporter)
            report = {"job": self.config.job, "stats": dict(self.stats), "finished_at": now_iso()}
            out_dir = Path(self.config.output.directory)
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / self.config.output.report_file).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            self.log("done records=%s failures=%s" % (self.stats["records"], self.stats["failures"]))
            return report
        finally:
            exporter.close()

    def run_source(self, source, exporter):
        fetcher = HttpFetcher(self.config.runtime.user_agent, self.config.runtime.timeout_seconds, self.config.runtime.max_retries)
        robots = RobotGuard(self.config.runtime.respect_robots_txt, self.config.runtime.user_agent)
        queue = deque(QueueItem(source.id, normalize_url(url) or url, 0, None, "") for url in source.start_urls)
        attempted = 0
        seed_url = source.start_urls[0] if source.start_urls else ""
        self.log("source=%s start max_pages=%s max_depth=%s" % (source.id, source.max_pages, source.max_depth))
        while queue and attempted < source.max_pages:
            item = queue.popleft()
            url = normalize_url(item.url)
            if not url:
                self.failure(exporter, source, item, "invalid_url")
                continue
            if self.config.runtime.deduplicate_url and url in self.url_seen:
                self.stats["skipped"] += 1
                continue
            ok, reason = allowed_by_rules(url, seed_url, source.url_rules)
            if not ok:
                self.stats["filtered_urls"] += 1
                continue
            ok, robots_reason = robots.allowed(url)
            if not ok:
                self.failure(exporter, source, item, "robots_denied", extra={"robots": robots_reason})
                continue
            self.wait_for_domain(url)
            attempted += 1
            self.url_seen.add(url)
            self.log("fetch source=%s %s/%s depth=%s url=%s" % (source.id, attempted, source.max_pages, item.depth, url))
            result = fetcher.fetch(url)
            if not result.get("ok"):
                self.failure(exporter, source, item, result.get("error_type") or "fetch_failed", result)
                continue
            record, parsed = self.build_record(source, item, url, result)
            if not self.keep_record(record, source):
                self.stats["filtered_records"] += 1
            else:
                exporter.write_record(record)
                self.stats["records"] += 1
            if source.discoverer == "html_links" and item.depth < source.max_depth and parsed and parsed.get("parser") == "html":
                for link in discover_html_links(parsed, result.get("final_url") or url):
                    if link_score(link, source.discovery) >= source.discovery.min_link_score:
                        queue.append(QueueItem(source.id, link["url"], item.depth + 1, url, link.get("context", "")))
        self.log("source=%s done attempted=%s records=%s failures=%s" % (source.id, attempted, self.stats["records"], self.stats["failures"]))

    def wait_for_domain(self, url):
        from urllib.parse import urlparse

        domain = urlparse(url).netloc
        elapsed = time.time() - self.last_request_at.get(domain, 0)
        wait = self.config.runtime.request_interval_seconds - elapsed
        if wait > 0:
            time.sleep(wait)
        self.last_request_at[domain] = time.time()

    def build_record(self, source, item, url, fetch_result):
        parser_name = detect_parser(fetch_result.get("final_url") or url, fetch_result.get("headers") or {}, source.parser)
        parsed = None
        if parser_name == "pdf":
            parsed = parse_pdf(fetch_result.get("body") or b"")
            extracted = extract_pdf(parsed)
        else:
            parsed = parse_html(fetch_result.get("body") or b"", fetch_result.get("headers") or {})
            extracted = extract_html(parsed, source.extraction)
        content_hash = sha256_text(extracted.get("content") or "")
        duplicate_content = self.config.runtime.deduplicate_content and content_hash in self.content_seen
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
            "content": extracted.get("content"),
            "content_hash": content_hash,
            "char_count": len(extracted.get("content") or ""),
            "fetcher": source.fetcher,
            "parser": parser_name,
            "extractor": source.extractor,
            "crawl_depth": item.depth,
            "parent_url": item.parent_url,
            "status": "ok",
            "http_status": fetch_result.get("status_code"),
            "crawl_time": now_iso(),
            "duplicate_content": duplicate_content,
            "metadata": source.metadata,
            "content_blocks": extracted.get("content_blocks") or [],
            "pdf_pages": extracted.get("pdf_pages") or [],
        }
        return record, parsed

    def keep_record(self, record, source):
        for rule in source.filters:
            if not isinstance(rule, dict):
                continue
            if rule.get("type") == "minimum_length" and record.get(rule.get("field", "content")):
                if len(record.get(rule.get("field", "content")) or "") < int(rule.get("value", 0)):
                    return False
            if rule.get("type") == "keyword_any":
                text = record.get(rule.get("field", "content")) or ""
                if not any(str(v) in text for v in rule.get("values", [])):
                    return False
        return True

    def failure(self, exporter, source, item, error_type, result=None, extra=None):
        failure = {
            "source_id": source.id,
            "url": item.url,
            "depth": item.depth,
            "error_type": error_type,
            "time": now_iso(),
        }
        if isinstance(result, dict):
            failure.update({k: result.get(k) for k in ("status_code", "retry_count", "exception") if result.get(k) is not None})
        if extra:
            failure.update(extra)
        exporter.write_failure(failure)
        self.stats["failures"] += 1
