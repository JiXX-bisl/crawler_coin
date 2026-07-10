# -*- coding: utf-8 -*-
"""
Targeted crawler for virtual-currency illegal/criminal case knowledge sources.

The implementation is intentionally dependency-light for the current Python 3.8
environment: requests + BeautifulSoup/lxml for HTML, pypdf for PDF when present.
"""

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import tempfile
import time
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, List, Optional, Sequence, Set, Tuple
from urllib import robotparser
from urllib.parse import parse_qsl, urlencode, urldefrag, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

try:
    from pypdf import PdfReader
except Exception:  # pragma: no cover - exercised by environments without pypdf.
    PdfReader = None  # type: ignore


DEFAULT_CONFIG_NAME = "virtual_currency_illegal_case_sources"
DEFAULT_CONFIG = Path("configs/virtual_currency_illegal_case_sources.json")
DEFAULT_OUTPUT_DIR = Path("data/virtual_currency_illegal_case_crawl")
DEFAULT_UA = "Mozilla/5.0 (compatible; VirtualCurrencyIllegalCaseCrawler/1.0; public legal research)"

TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "spm",
    "from",
    "session",
    "timestamp",
    "fbclid",
    "gclid",
    "yclid",
    "_ga",
}

UNSUPPORTED_EXTENSIONS = re.compile(
    r"\.(?:jpg|jpeg|png|gif|webp|svg|css|js|mjs|mp4|mp3|avi|mov|wmv|zip|rar|7z|tar|gz|apk|exe|dmg)(?:\?.*)?$",
    re.I,
)

PDF_EXTENSION = re.compile(r"\.pdf(?:\?.*)?$", re.I)

TOPIC_TERMS = [
    "\u6848\u4ef6",
    "\u6848\u4f8b",
    "\u53f8\u6cd5",
    "\u6cd5\u9662",
    "\u68c0\u5bdf",
    "\u516c\u5b89",
    "\u5224\u51b3",
    "\u88c1\u5224",
    "\u72af\u7f6a",
    "\u5211\u4e8b",
    "\u53cd\u8bc8",
    "\u8bc8\u9a97",
    "\u6d17\u94b1",
    "\u975e\u6cd5",
    "\u4f20\u9500",
    "\u865a\u62df\u8d27\u5e01",
    "\u6570\u5b57\u8d27\u5e01",
    "\u52a0\u5bc6\u8d27\u5e01",
    "\u533a\u5757\u94fe",
    "\u6bd4\u7279\u5e01",
    "\u6cf0\u8fbe\u5e01",
    "\u6570\u5b57\u85cf\u54c1",
    "usdt",
    "btc",
    "eth",
    "nft",
]
DETAIL_HINTS = re.compile(
    r"(xiangqing|detail|content|article|case|news|zixun|anli|wenshu|cpws|"
    r"show|info|html)",
    re.I,
)
LIST_HINTS = re.compile(r"(list|search|topic|zhuanti|zt|page|index|column|channel)", re.I)
NAV_PATH_HINTS = re.compile(r"/(?:business|office|team|lawyer|about|contact|honor|party|social|publish|paper)(?:[_/-]|$)", re.I)
STRONG_LINK_TERMS = [
    "\u6848\u4ef6", "\u6848\u4f8b", "\u72af\u7f6a", "\u53cd\u8bc8", "\u8bc8\u9a97",
    "\u6d17\u94b1", "\u975e\u6cd5", "\u4f20\u9500", "\u865a\u62df\u8d27\u5e01",
    "\u6570\u5b57\u8d27\u5e01", "\u52a0\u5bc6\u8d27\u5e01", "\u533a\u5757\u94fe",
    "\u6bd4\u7279\u5e01", "\u6cf0\u8fbe\u5e01", "\u6570\u5b57\u85cf\u54c1",
    "usdt", "btc", "eth", "nft",
]
LOGIN_CAPTCHA_HINTS = re.compile(r"(鐧诲綍|娉ㄥ唽|楠岃瘉鐮亅captcha|login required|sign in|浠樿垂|paywall)", re.I)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", errors="ignore")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data or b"").hexdigest()


def as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, tuple):
        return [str(x).strip() for x in value if str(x).strip()]
    text = str(value).strip()
    if not text:
        return []
    return [x.strip() for x in re.split(r"[,;|锛岋紱銆乚+", text) if x.strip()]


def normalize_text(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\xa0", " ").replace("\u200b", "")
    text = re.sub(r"[ \t\x0b\x0c]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    lines: List[str] = []
    last = None
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line == last:
            continue
        lines.append(line)
        last = line
    return "\n".join(lines).strip()


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def find_config_path(path_arg: Optional[str]) -> Path:
    if path_arg:
        return Path(path_arg)
    if DEFAULT_CONFIG.exists():
        return DEFAULT_CONFIG
    for candidate in Path("configs").glob("*.json"):
        try:
            data = load_json(candidate)
        except Exception:
            continue
        if data.get("config_name") == DEFAULT_CONFIG_NAME:
            return candidate
    raise FileNotFoundError("Could not find virtual_currency_illegal_case_sources config")


def normalize_url(url: Any, base_url: Optional[str] = None) -> Optional[str]:
    if url is None:
        return None
    if isinstance(url, (list, tuple)):
        url = url[0] if url else None
    if url is None:
        return None
    url = str(url).strip()
    if not url:
        return None
    if re.match(r"^(?:mailto|javascript|tel|data):", url, re.I):
        return None
    if base_url:
        url = urljoin(base_url, url)
    url, _ = urldefrag(url)
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ("http", "https"):
        return None
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if not hostname:
        return None
    try:
        hostname = hostname.encode("idna").decode("ascii")
    except Exception:
        pass
    port = parsed.port
    netloc = hostname
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = "%s:%s" % (hostname, port)
    path = parsed.path or "/"
    path = re.sub(r"/{2,}", "/", path)
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    params = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower() in TRACKING_PARAMS:
            continue
        params.append((key, value))
    query = urlencode(sorted(params), doseq=True)
    return urlunparse((scheme, netloc, path, "", query, ""))


def domain_matches(hostname: str, allowed_domain: str, allow_subdomains: bool = True) -> bool:
    hostname = (hostname or "").lower().rstrip(".")
    allowed_domain = (allowed_domain or "").lower().rstrip(".")
    if not hostname or not allowed_domain:
        return False
    return hostname == allowed_domain or (allow_subdomains and hostname.endswith("." + allowed_domain))


def url_domain_allowed(url: str, domain: str, seed_url: Optional[str] = None, follow_external: bool = False) -> bool:
    parsed = urlparse(url)
    if follow_external:
        return True
    domains = [domain]
    if seed_url:
        seed_host = urlparse(seed_url).hostname or ""
        if seed_host and seed_host not in domains:
            domains.append(seed_host)
    return any(domain_matches(parsed.hostname or "", d, True) for d in domains if d)


def compile_patterns(patterns: Iterable[str], kind: str, source_id: str) -> Tuple[List[re.Pattern], List[Dict[str, Any]]]:
    compiled: List[re.Pattern] = []
    errors: List[Dict[str, Any]] = []
    for pattern in patterns:
        try:
            compiled.append(re.compile(str(pattern), re.I))
        except re.error as exc:
            errors.append(
                {
                    "source_id": source_id,
                    "error_type": "config_regex_error",
                    "pattern_kind": kind,
                    "pattern": pattern,
                    "exception": str(exc),
                    "time": now_iso(),
                }
            )
    return compiled, errors


@dataclass
class SourceRuntime:
    raw: Dict[str, Any]
    global_policy: Dict[str, Any]
    allow_patterns: List[re.Pattern]
    deny_patterns: List[re.Pattern]
    regex_errors: List[Dict[str, Any]]

    @property
    def source_id(self) -> str:
        return str(self.raw.get("source_id") or self.raw.get("id") or self.raw.get("name") or "unknown")

    @property
    def seed_url(self) -> str:
        return str(self.raw.get("seed_url") or self.raw.get("url") or "")

    @property
    def domain(self) -> str:
        return str(self.raw.get("domain") or (urlparse(self.seed_url).hostname or "")).lower()

    def policy(self, key: str, default: Any = None) -> Any:
        if self.raw.get(key) is not None:
            return self.raw.get(key)
        return self.global_policy.get(key, default)

    def max_depth(self, override: Optional[int] = None) -> int:
        if override is not None:
            return override
        return int(self.raw.get("crawl_depth") or self.global_policy.get("max_default_crawl_depth") or 1)

    def max_pages(self, override: Optional[int] = None) -> int:
        if override is not None:
            return override
        return int(self.raw.get("max_pages_per_seed") or self.global_policy.get("max_pages_per_seed") or 50)

    def interval(self, override: Optional[float] = None) -> float:
        if override is not None:
            return float(override)
        return float(self.raw.get("request_interval_seconds") or self.global_policy.get("request_interval_seconds") or 1.0)

    def user_agent(self) -> str:
        return str(self.raw.get("user_agent") or self.global_policy.get("user_agent") or DEFAULT_UA)

    def follow_external(self) -> bool:
        return bool(self.policy("follow_external_links", False))

    def retain_pdf(self) -> bool:
        return bool(self.policy("retain_pdf_documents", True))


def make_sources(config: Dict[str, Any]) -> List[SourceRuntime]:
    global_policy = config.get("global_crawl_policy") or {}
    result = []
    for raw in config.get("sources") or []:
        sid = str(raw.get("source_id") or raw.get("id") or "unknown")
        allow, allow_err = compile_patterns(as_list(raw.get("allow_url_patterns")), "allow", sid)
        deny, deny_err = compile_patterns(as_list(raw.get("deny_url_patterns")), "deny", sid)
        result.append(SourceRuntime(raw, global_policy, allow, deny, allow_err + deny_err))
    return result


def pattern_match(patterns: Sequence[re.Pattern], text: str) -> bool:
    return any(p.search(text or "") for p in patterns)


def url_allowed_by_config(url: str, source: SourceRuntime, expansion_mode: str = "strict", link_context: str = "") -> Dict[str, Any]:
    normalized = normalize_url(url)
    if not normalized:
        return {"allowed": False, "reason": "invalid_url", "normalized_url": None}
    if UNSUPPORTED_EXTENSIONS.search(normalized) and not PDF_EXTENSION.search(normalized):
        return {"allowed": False, "reason": "unsupported_resource_type", "normalized_url": normalized}
    if PDF_EXTENSION.search(normalized) and not source.retain_pdf():
        return {"allowed": False, "reason": "pdf_disabled", "normalized_url": normalized}
    if not url_domain_allowed(normalized, source.domain, source.seed_url, source.follow_external()):
        return {"allowed": False, "reason": "domain_not_allowed", "normalized_url": normalized}
    if pattern_match(source.deny_patterns, normalized):
        return {"allowed": False, "reason": "deny_pattern", "normalized_url": normalized}
    allow_hit = pattern_match(source.allow_patterns, normalized) if source.allow_patterns else True
    if allow_hit:
        return {"allowed": True, "reason": "allow_pattern", "mode": "strict", "normalized_url": normalized}
    if expansion_mode == "strict":
        return {"allowed": False, "reason": "not_allowed_by_strict_patterns", "normalized_url": normalized}
    controlled = controlled_link_allowed(normalized, source, link_context)
    return {
        "allowed": controlled,
        "reason": "controlled_topic_match" if controlled else "controlled_topic_miss",
        "mode": "controlled",
        "normalized_url": normalized,
    }


def same_seed_section(url_path: str, seed_path: str) -> bool:
    url_parts = [p.lower() for p in url_path.split("/") if p]
    seed_parts = [p.lower() for p in seed_path.split("/") if p]
    if not url_parts or not seed_parts:
        return False
    compare_len = 2 if len(seed_parts) >= 2 and len(url_parts) >= 2 else 1
    return url_parts[:compare_len] == seed_parts[:compare_len]


def controlled_link_allowed(url: str, source: SourceRuntime, link_context: str = "") -> bool:
    if pattern_match(source.deny_patterns, url):
        return False
    if not url_domain_allowed(url, source.domain, source.seed_url, False):
        return False
    parsed = urlparse(url)
    context = normalize_text(link_context or "")
    haystack = " ".join([parsed.path, parsed.query, context]).lower()
    strong_topic_hit = any(term.lower() in haystack for term in STRONG_LINK_TERMS)
    if NAV_PATH_HINTS.search(parsed.path) and not strong_topic_hit:
        return False
    if strong_topic_hit:
        return True
    if not context or len(context) < 4:
        return False
    weak_nav = re.compile(r"^(home|about|contact|office|lawyer|team|honor|party|news|more|index|business|publish|paper|\u9996\u9875|\u5173\u4e8e|\u8054\u7cfb|\u5f8b\u5e08|\u56e2\u961f|\u8363\u8a89|\u66f4\u591a|\u4e1a\u52a1|\u65b0\u95fb)$", re.I)
    if weak_nav.search(context.strip()):
        return False
    seed_path = urlparse(source.seed_url).path
    if same_seed_section(parsed.path, seed_path) and (DETAIL_HINTS.search(parsed.path) or LIST_HINTS.search(parsed.path)):
        return True
    return False

def seed_self_check(source: SourceRuntime) -> Dict[str, Any]:
    seed = normalize_url(source.seed_url)
    if not seed:
        return {"ok": False, "reason": "invalid_seed_url", "seed_url": source.seed_url}
    if source.allow_patterns and not pattern_match(source.allow_patterns, seed):
        return {"ok": False, "reason": "seed_not_allowed_by_config", "seed_url": source.seed_url, "normalized_url": seed}
    return {"ok": True, "reason": "seed_allowed", "seed_url": source.seed_url, "normalized_url": seed}


class RobotCache:
    def __init__(self, user_agent: str, respect: bool = True, timeout: int = 10):
        self.user_agent = user_agent
        self.respect = respect
        self.timeout = timeout
        self.parsers: Dict[str, Tuple[Optional[robotparser.RobotFileParser], Optional[str]]] = {}

    def can_fetch(self, url: str) -> Tuple[bool, Dict[str, Any]]:
        if not self.respect:
            return True, {"checked": False, "allowed": True, "reason": "robots_disabled"}
        parsed = urlparse(url)
        base = "%s://%s" % (parsed.scheme, parsed.netloc)
        if base not in self.parsers:
            rp = robotparser.RobotFileParser()
            rp.set_url(urljoin(base, "/robots.txt"))
            try:
                rp.read()
                self.parsers[base] = (rp, None)
            except Exception as exc:
                self.parsers[base] = (None, str(exc))
        rp, err = self.parsers[base]
        if err or rp is None:
            return True, {"checked": True, "allowed": True, "reason": "robots_fetch_failed", "error": err}
        allowed = rp.can_fetch(self.user_agent, url)
        return allowed, {"checked": True, "allowed": allowed, "reason": "robots_allowed" if allowed else "robots_denied"}


def request_with_retries(
    session: requests.Session,
    url: str,
    timeout: int = 30,
    max_retries: int = 2,
    max_bytes: int = 15 * 1024 * 1024,
) -> Dict[str, Any]:
    retry_statuses = {429, 500, 502, 503, 504}
    no_retry_statuses = {400, 401, 403, 404, 410, 451}
    attempts = 0
    last_exc = None
    while attempts <= max_retries:
        try:
            resp = session.get(url, timeout=timeout, allow_redirects=True, stream=True)
            status = resp.status_code
            if status in retry_statuses and attempts < max_retries:
                attempts += 1
                time.sleep(min(2 ** attempts, 8))
                continue
            chunks: List[bytes] = []
            total = 0
            for chunk in resp.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    return {
                        "ok": False,
                        "error_type": "content_too_large",
                        "status_code": status,
                        "retry_count": attempts,
                        "final_url": resp.url,
                        "headers": dict(resp.headers),
                    }
                chunks.append(chunk)
            body = b"".join(chunks)
            ok = 200 <= status < 300
            if not ok:
                et = "http_error_no_retry" if status in no_retry_statuses else "http_error"
                return {
                    "ok": False,
                    "error_type": et,
                    "status_code": status,
                    "retry_count": attempts,
                    "final_url": resp.url,
                    "headers": dict(resp.headers),
                    "body": body[:4096],
                }
            return {
                "ok": True,
                "status_code": status,
                "retry_count": attempts,
                "final_url": resp.url,
                "headers": dict(resp.headers),
                "body": body,
            }
        except requests.Timeout as exc:
            last_exc = exc
            if attempts >= max_retries:
                break
            attempts += 1
            time.sleep(min(2 ** attempts, 8))
        except requests.ConnectionError as exc:
            last_exc = exc
            if attempts >= max_retries:
                break
            attempts += 1
            time.sleep(min(2 ** attempts, 8))
        except Exception as exc:
            last_exc = exc
            break
    error_type = "network_timeout" if isinstance(last_exc, requests.Timeout) else "network_connection_error"
    return {"ok": False, "error_type": error_type, "retry_count": attempts, "exception": repr(last_exc)}


def decode_body(body: bytes, headers: Dict[str, str]) -> Tuple[str, str]:
    ctype = headers.get("Content-Type", "") or headers.get("content-type", "")
    match = re.search(r"charset=([\w.-]+)", ctype, re.I)
    encodings = [match.group(1)] if match else []
    encodings.extend(["utf-8", "gb18030", "gbk"])
    for enc in encodings:
        try:
            return body.decode(enc), enc
        except Exception:
            continue
    return body.decode("utf-8", errors="replace"), "utf-8"


def extract_metadata(soup: BeautifulSoup) -> Dict[str, Any]:
    title = ""
    if soup.find("h1"):
        title = normalize_text(soup.find("h1").get_text(" ", strip=True))
    if not title and soup.title:
        title = normalize_text(soup.title.get_text(" ", strip=True))
    metadata: Dict[str, Any] = {
        "title": title or None,
        "author": None,
        "source": None,
        "published_at": None,
        "description": None,
    }
    meta_map = {
        "author": ["author", "article:author"],
        "source": ["source", "mediaid", "publisher"],
        "published_at": ["pubdate", "publishdate", "publish_date", "article:published_time", "date"],
        "description": ["description", "og:description"],
    }
    for key, names in meta_map.items():
        for name in names:
            tag = soup.find("meta", attrs={"name": re.compile("^%s$" % re.escape(name), re.I)}) or soup.find(
                "meta", attrs={"property": re.compile("^%s$" % re.escape(name), re.I)}
            )
            if tag and tag.get("content"):
                metadata[key] = normalize_text(str(tag.get("content")))
                break
    for script in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)}):
        try:
            obj = json.loads(script.get_text("", strip=True))
        except Exception:
            continue
        items = obj if isinstance(obj, list) else [obj]
        for item in items:
            if not isinstance(item, dict):
                continue
            metadata["title"] = metadata["title"] or item.get("headline") or item.get("name")
            metadata["published_at"] = metadata["published_at"] or item.get("datePublished")
            author = item.get("author")
            if not metadata["author"] and isinstance(author, dict):
                metadata["author"] = author.get("name")
    return metadata


def clean_soup(soup: BeautifulSoup) -> None:
    for tag in soup(["script", "style", "noscript", "svg", "canvas", "iframe", "form", "input", "button"]):
        tag.decompose()
    bad_regex = re.compile(r"(nav|footer|header|aside|comment|share|recommend|related|advert|ad-|login|breadcrumb)", re.I)
    for tag in list(soup.find_all(True)):
        if getattr(tag, "attrs", None) is None:
            continue
        joined = " ".join(as_list(tag.get("class")) + [str(tag.get("id") or "")])
        if bad_regex.search(joined):
            tag.decompose()


def select_main_container(soup: BeautifulSoup) -> Any:
    selectors = [
        "article",
        "main",
        "[role=main]",
        ".article",
        ".article-content",
        ".content",
        ".content-main",
        ".main-content",
        ".TRS_Editor",
        "#content",
        "#main",
    ]
    candidates = []
    for selector in selectors:
        candidates.extend(soup.select(selector))
    if not candidates:
        candidates = soup.find_all(["div", "section", "td"]) or [soup.body or soup]
    best = soup.body or soup
    best_score = -1
    for node in candidates:
        text = normalize_text(node.get_text("\n", strip=True))
        p_count = len(node.find_all("p"))
        link_text = " ".join(a.get_text(" ", strip=True) for a in node.find_all("a"))
        link_ratio = len(link_text) / max(len(text), 1)
        score = len(text) + p_count * 80 - int(link_ratio * 600)
        if score > best_score:
            best_score = score
            best = node
    return best


def table_to_matrix(table: Any) -> List[List[str]]:
    rows: List[List[str]] = []
    for tr in table.find_all("tr"):
        cells = [normalize_text(c.get_text(" ", strip=True)) for c in tr.find_all(["th", "td"])]
        if any(cells):
            rows.append(cells)
    return rows


def extract_content_blocks(root: Any) -> List[Dict[str, Any]]:
    blocks: List[Dict[str, Any]] = []
    order = 0

    def add(kind: str, text: str, extra: Optional[Dict[str, Any]] = None) -> None:
        nonlocal order
        text = normalize_text(text)
        if not text and kind != "table":
            return
        block = {"order": order, "type": kind, "text": text}
        if extra:
            block.update(extra)
        blocks.append(block)
        order += 1

    def walk(node: Any) -> None:
        if getattr(node, "name", None) is None:
            return
        name = node.name.lower()
        if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            add("heading", node.get_text(" ", strip=True), {"level": int(name[1])})
            return
        if name == "p":
            add("paragraph", node.get_text(" ", strip=True))
            return
        if name in ("ul", "ol"):
            items = [normalize_text(li.get_text(" ", strip=True)) for li in node.find_all("li", recursive=False)]
            items = [x for x in items if x]
            if items:
                add("list", "\n".join(items), {"items": items, "ordered": name == "ol"})
            return
        if name == "table":
            rows = table_to_matrix(node)
            if rows:
                add("table", "\n".join(" | ".join(r) for r in rows), {"rows": rows})
            return
        for child in list(getattr(node, "children", [])):
            walk(child)

    walk(root)
    if not blocks:
        text = normalize_text(root.get_text("\n", strip=True))
        for para in [x for x in text.split("\n") if x.strip()]:
            add("paragraph", para)
    return blocks


def derive_text_fields(blocks: List[Dict[str, Any]]) -> Dict[str, Any]:
    headings = [b for b in blocks if b.get("type") == "heading"]
    paragraphs = [b for b in blocks if b.get("type") == "paragraph"]
    lists = [b for b in blocks if b.get("type") == "list"]
    tables = [b for b in blocks if b.get("type") == "table"]
    full_text = "\n".join(b.get("text") or "" for b in sorted(blocks, key=lambda x: x["order"]) if b.get("text"))
    return {"headings": headings, "paragraphs": paragraphs, "lists": lists, "tables": tables, "full_text": full_text}


def extract_links(soup: BeautifulSoup, base_url: str) -> List[Dict[str, str]]:
    dedup: Dict[str, Dict[str, str]] = {}
    for a in soup.find_all("a", href=True):
        href = a.get("href")
        u = normalize_url(href, base_url)
        if not u:
            continue
        anchor_text = normalize_text(a.get_text(" ", strip=True))[:300]
        parent_text = normalize_text(a.parent.get_text(" ", strip=True) if a.parent else "")
        sibling_link_count = len(a.parent.find_all("a")) if a.parent else 0
        if len(parent_text) > 240 or sibling_link_count > 1:
            parent_text = anchor_text
        item = {
            "url": u,
            "anchor_text": anchor_text,
            "context": parent_text[:300],
        }
        dedup.setdefault(u, item)
    return [dedup[k] for k in sorted(dedup)]


def extract_html(body: bytes, final_url: str, headers: Dict[str, str]) -> Dict[str, Any]:
    html, encoding = decode_body(body, headers)
    soup = BeautifulSoup(html, "lxml")
    metadata = extract_metadata(soup)
    links = extract_links(soup, final_url)
    clean_soup(soup)
    root = select_main_container(soup)
    blocks = extract_content_blocks(root)
    fields = derive_text_fields(blocks)
    quality_flags: List[str] = []
    if LOGIN_CAPTCHA_HINTS.search(fields["full_text"][:1200]):
        quality_flags.append("login_or_captcha_or_paywall_detected")
    if len(fields["full_text"]) < 120:
        quality_flags.append("short_text")
    return {
        "metadata": metadata,
        "content_blocks": blocks,
        "links": links,
        "encoding": encoding,
        "extraction_method": "beautifulsoup_dom_scoring",
        "quality_flags": quality_flags,
        **fields,
    }


def extract_pdf(body: bytes) -> Dict[str, Any]:
    pages: List[Dict[str, Any]] = []
    flags: List[str] = []
    if PdfReader is None:
        return {
            "metadata": {"title": None, "author": None, "source": None, "published_at": None, "description": None},
            "content_blocks": [],
            "headings": [],
            "paragraphs": [],
            "lists": [],
            "tables": [],
            "pdf_pages": [],
            "full_text": "",
            "encoding": None,
            "extraction_method": "pdf_unavailable",
            "quality_flags": ["pdf_text_extraction_failed"],
        }
    try:
        reader = PdfReader(BytesIO(body))
        for idx, page in enumerate(reader.pages, start=1):
            text = normalize_text(page.extract_text() or "")
            pages.append({"page_number": idx, "text": text})
    except Exception:
        flags.append("pdf_text_extraction_failed")
    full_text = "\n".join(p["text"] for p in pages if p["text"])
    if len(full_text) < 80:
        flags.extend(["pdf_text_extraction_failed", "possible_scanned_pdf"])
    blocks = [
        {"order": idx, "type": "pdf_page", "text": p["text"], "page_number": p["page_number"]}
        for idx, p in enumerate(pages)
        if p["text"]
    ]
    return {
        "metadata": {"title": None, "author": None, "source": None, "published_at": None, "description": None},
        "content_blocks": blocks,
        "headings": [],
        "paragraphs": [{"order": b["order"], "type": "paragraph", "text": b["text"]} for b in blocks],
        "lists": [],
        "tables": [],
        "pdf_pages": pages,
        "full_text": full_text,
        "encoding": None,
        "extraction_method": "pypdf",
        "quality_flags": sorted(set(flags)),
    }


CASE_NUMBER_RE = re.compile(r"[\(\uff08]?\d{4}[\)\uff09]?[\u4e00-\u9fa5A-Za-z0-9]{0,20}(?:\u5211|\u6c11|\u884c|\u6267|\u8d54|\u518d|\u7533|\u7ec8|\u521d|\u5b57|\u53f7)[\u4e00-\u9fa5A-Za-z0-9\u7b2c\-\uff08\uff09()]*\u53f7")
ORG_RE = re.compile(r"[\u4e00-\u9fa5]{2,40}(?:\u4eba\u6c11\u6cd5\u9662|\u4eba\u6c11\u68c0\u5bdf\u9662|\u516c\u5b89\u5c40|\u76d1\u5bdf\u59d4\u5458\u4f1a|\u6cd5\u9662|\u68c0\u5bdf\u9662|\u516c\u5b89\u673a\u5173|\u6d3e\u51fa\u6240|\u53f8\u6cd5\u5c40)")
CRIME_RE = re.compile(r"(?:\u8bc8\u9a97\u7f6a|\u6d17\u94b1\u7f6a|\u975e\u6cd5\u5438\u6536\u516c\u4f17\u5b58\u6b3e\u7f6a|\u96c6\u8d44\u8bc8\u9a97\u7f6a|\u7ec4\u7ec7\u3001\u9886\u5bfc\u4f20\u9500\u6d3b\u52a8\u7f6a|\u63a9\u9970\u3001\u9690\u7792\u72af\u7f6a\u6240\u5f97(?:\u3001\u72af\u7f6a\u6240\u5f97\u6536\u76ca)?\u7f6a|\u5e2e\u52a9\u4fe1\u606f\u7f51\u7edc\u72af\u7f6a\u6d3b\u52a8\u7f6a|\u975e\u6cd5\u7ecf\u8425\u7f6a|\u76d7\u7a83\u7f6a|\u4f20\u9500\u72af\u7f6a|\u7535\u4fe1\u7f51\u7edc\u8bc8\u9a97)")
MONEY_RE = re.compile(r"(?:\u4eba\u6c11\u5e01|\u7f8e\u5143|\u6e2f\u5e01|\u6b27\u5143|USDT|usdt|\u6bd4\u7279\u5e01|BTC|ETH|\u6cf0\u8fbe\u5e01)?\s*\d+(?:[.,]\d+)*(?:\.\d+)?\s*(?:\u4e07\u5143|\u4ebf\u5143|\u5143|\u7f8e\u5143|\u679a|\u4e2a|USDT|usdt|BTC|ETH|\u6bd4\u7279\u5e01|\u6cf0\u8fbe\u5e01)")
DATE_RE = re.compile(r"\d{4}\u5e74\d{1,2}\u6708\d{1,2}\u65e5|\d{4}[-/.]\d{1,2}[-/.]\d{1,2}")
LAW_RE = re.compile(r"\u300a[^\u300b]{2,40}\u300b\u7b2c[\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341\u767e\u5343\u4e07\u96f6\d]+\u6761(?:\u7b2c[\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341\u767e\u5343\u4e07\u96f6\d]+\u6b3e)?")
VC_RE = re.compile(r"(\u865a\u62df\u8d27\u5e01|\u6570\u5b57\u8d27\u5e01|\u52a0\u5bc6\u8d27\u5e01|\u533a\u5757\u94fe|USDT|usdt|\u6cf0\u8fbe\u5e01|\u6bd4\u7279\u5e01|BTC|ETH|NFT|\u6570\u5b57\u85cf\u54c1|\u94b1\u5305\u5730\u5740)")

def unique_matches(pattern: re.Pattern, text: str, limit: int = 80) -> List[str]:
    seen: Set[str] = set()
    result: List[str] = []
    for match in pattern.finditer(text or ""):
        value = normalize_text(match.group(0))
        if value and value not in seen:
            seen.add(value)
            result.append(value)
        if len(result) >= limit:
            break
    return result


def rule_extract(text: str) -> Dict[str, Any]:
    return {
        "case_numbers": unique_matches(CASE_NUMBER_RE, text),
        "institutions": unique_matches(ORG_RE, text),
        "crimes": unique_matches(CRIME_RE, text),
        "amounts": unique_matches(MONEY_RE, text),
        "dates": unique_matches(DATE_RE, text),
        "legal_articles": unique_matches(LAW_RE, text),
        "virtual_currency_keywords": unique_matches(VC_RE, text),
    }


def relevance_score(title: str, text: str, extracted: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    title = title or ""
    text = text or ""
    title_hits = [t for t in TOPIC_TERMS if t.lower() in title.lower()]
    body_hits = [t for t in TOPIC_TERMS if t.lower() in text.lower()]
    score = 0
    score += min(len(title_hits) * 10, 30)
    score += min(len(body_hits) * 4, 28)
    score += 10 if extracted["case_numbers"] else 0
    score += 8 if extracted["institutions"] else 0
    score += 8 if extracted["amounts"] else 0
    score += 10 if extracted["crimes"] else 0
    score += 6 if extracted["legal_articles"] else 0
    score += 8 if len(text) >= 800 else (4 if len(text) >= 200 else 0)
    basis = {
        "title_keyword_hits": title_hits,
        "body_keyword_hits": body_hits[:20],
        "has_case_number": bool(extracted["case_numbers"]),
        "has_institution": bool(extracted["institutions"]),
        "has_amount": bool(extracted["amounts"]),
        "has_crime": bool(extracted["crimes"]),
        "has_legal_article": bool(extracted["legal_articles"]),
        "text_length": len(text),
    }
    return min(score, 100), basis


def atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp_name, str(path))
    finally:
        if os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


def load_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {
            "version": 1,
            "config_hash": None,
            "successful_urls": [],
            "failed_urls": [],
            "content_hashes": [],
            "record_ids": [],
            "updated_at": None,
        }
    try:
        return load_json(path)
    except Exception:
        return {
            "version": 1,
            "config_hash": None,
            "successful_urls": [],
            "failed_urls": [],
            "content_hashes": [],
            "record_ids": [],
            "updated_at": None,
            "state_load_warning": "failed_to_parse_existing_state",
        }


def jsonl_record_ids(path: Path) -> Set[str]:
    ids: Set[str] = set()
    if not path.exists():
        return ids
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
                if obj.get("record_id"):
                    ids.add(obj["record_id"])
            except Exception:
                continue
    return ids


def append_jsonl(handle: Any, obj: Dict[str, Any]) -> None:
    handle.write(json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n")
    handle.flush()


class ProgressReporter:
    def __init__(self, enabled: bool = True, every: int = 1):
        self.enabled = enabled
        self.every = max(int(every or 1), 1)
        self.last_message = ""

    def emit(self, message: str, force: bool = False) -> None:
        logging.info(message)
        if not self.enabled:
            return
        if force or message != self.last_message:
            print("[%s] %s" % (now_iso(), message), flush=True)
            self.last_message = message

    def page_event(
        self,
        attempted: int,
        message: str,
        force: bool = False,
    ) -> None:
        if force or attempted <= 1 or attempted % self.every == 0:
            self.emit(message, force=force)


def make_record_id(source_id: str, normalized_url: str) -> str:
    return "%s_%s" % (source_id, sha256_text(normalized_url)[:16])


def content_type_kind(url: str, headers: Dict[str, str]) -> str:
    ctype = (headers.get("Content-Type") or headers.get("content-type") or "").lower()
    if "pdf" in ctype or PDF_EXTENSION.search(url):
        return "pdf"
    if "html" in ctype or "xml" in ctype or not ctype:
        return "html"
    if "text/plain" in ctype:
        return "html"
    return "unsupported"


def build_record(
    run_id: str,
    source: SourceRuntime,
    seed_url: str,
    request_url: str,
    parent_url: Optional[str],
    depth: int,
    fetch_result: Dict[str, Any],
    extraction: Dict[str, Any],
    discovered_links: List[Dict[str, str]],
    validation: Dict[str, Any],
    seen_content_hashes: Set[str],
    content_duplicate_of: Optional[str] = None,
) -> Dict[str, Any]:
    final_url = fetch_result.get("final_url") or request_url
    normalized_final = normalize_url(final_url) or final_url
    full_text = extraction.get("full_text") or ""
    content_hash = sha256_text(full_text)
    extracted = rule_extract(full_text)
    score, basis = relevance_score((extraction.get("metadata") or {}).get("title") or "", full_text, extracted)
    is_content_duplicate = content_hash in seen_content_hashes if content_hash else False
    record_id = make_record_id(source.source_id, normalized_final)
    link_stats = Counter()
    for link in discovered_links:
        check = url_allowed_by_config(link["url"], source, "controlled", " ".join([link.get("anchor_text", ""), link.get("context", "")]))
        link_stats[check["reason"]] += 1
    return {
        "run_id": run_id,
        "record_id": record_id,
        "source": {
            "source_id": source.source_id,
            "source_name": source.raw.get("source_name"),
            "publisher": source.raw.get("publisher"),
            "source_type": source.raw.get("source_type"),
            "authority_level": source.raw.get("authority_level"),
            "official_status": source.raw.get("official_status"),
            "region": source.raw.get("region"),
            "language": source.raw.get("language"),
            "domain": source.domain,
            "entry_type": source.raw.get("entry_type"),
            "priority": source.raw.get("priority"),
            "crime_types_config": source.raw.get("crime_types") or [],
        },
        "seed_url": seed_url,
        "request_url": request_url,
        "final_url": final_url,
        "normalized_url": normalized_final,
        "parent_url": parent_url,
        "crawl_depth": depth,
        "http_status": fetch_result.get("status_code"),
        "content_type": (fetch_result.get("headers") or {}).get("Content-Type")
        or (fetch_result.get("headers") or {}).get("content-type"),
        "encoding": extraction.get("encoding"),
        "fetched_at": now_iso(),
        "metadata": extraction.get("metadata") or {},
        "content_blocks": extraction.get("content_blocks") or [],
        "headings": extraction.get("headings") or [],
        "paragraphs": extraction.get("paragraphs") or [],
        "lists": extraction.get("lists") or [],
        "tables": extraction.get("tables") or [],
        "pdf_pages": extraction.get("pdf_pages") or [],
        "full_text": full_text,
        "text_length": len(full_text),
        "content_hash": content_hash,
        "rule_extractions": extracted,
        "discovered_links": {
            "total": len(discovered_links),
            "by_validation_reason": dict(link_stats),
        },
        "relevance_score": score,
        "relevance_basis": basis,
        "duplicates": {
            "url_duplicate": False,
            "content_duplicate": is_content_duplicate,
            "content_duplicate_of": content_duplicate_of,
        },
        "extraction_method": extraction.get("extraction_method"),
        "quality_flags": extraction.get("quality_flags") or [],
        "validation": validation,
        "error": None,
    }


def failure_record(
    source: Optional[SourceRuntime],
    url: Optional[str],
    depth: Optional[int],
    error_type: str,
    status_code: Optional[int] = None,
    retry_count: int = 0,
    exception: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    obj = {
        "source_id": source.source_id if source else None,
        "source_name": source.raw.get("source_name") if source else None,
        "url": url,
        "depth": depth,
        "time": now_iso(),
        "error_type": error_type,
        "status_code": status_code,
        "retry_count": retry_count,
        "exception": exception,
    }
    if extra:
        obj.update(extra)
    return obj


def make_session(user_agent: str) -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf,text/plain,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
    )
    return s


def classify_link_patterns(links: List[Dict[str, str]]) -> Dict[str, Any]:
    path_shapes = Counter()
    for link in links:
        path = urlparse(link["url"]).path
        shape = re.sub(r"\d+", "{num}", path)
        shape = re.sub(r"[a-f0-9]{8,}", "{hash}", shape, flags=re.I)
        if shape:
            path_shapes[shape] += 1
    return {"inferred_path_patterns": [k for k, _ in path_shapes.most_common(10)]}


def dry_run(config_path: Path, output_dir: Path, args: argparse.Namespace) -> int:
    config = load_json(config_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    sources = make_sources(config)
    if args.source_id:
        wanted = set(args.source_id)
        sources = [s for s in sources if s.source_id in wanted]
    failures: List[Dict[str, Any]] = []
    expansion: List[Dict[str, Any]] = []
    for source in sources:
        for err in source.regex_errors:
            failures.append(err)
        check = seed_self_check(source)
        if not check["ok"]:
            failures.append(failure_record(source, source.seed_url, 0, check["reason"], extra=check))
        expansion.append(
            {
                "source_id": source.source_id,
                "seed_url": source.seed_url,
                "seed_check": check,
                "expansion_mode": args.expansion_mode,
                "allow_pattern_count": len(source.allow_patterns),
                "deny_pattern_count": len(source.deny_patterns),
                "candidate_count": 0,
                "accepted_candidate_count": 0,
                "derived_patterns": [],
                "cannot_expand_reason": "dry_run_no_network_fetch",
            }
        )
    report = {
        "run_id": "dry_%s" % now_iso(),
        "config_path": str(config_path),
        "config_hash": sha256_text(config_path.read_text(encoding="utf-8")),
        "source_count": len(sources),
        "regex_error_count": len([f for f in failures if f.get("error_type") == "config_regex_error"]),
        "seed_not_allowed_count": len([f for f in failures if f.get("error_type") == "seed_not_allowed_by_config"]),
        "dry_run": True,
        "generated_at": now_iso(),
    }
    atomic_write_json(output_dir / "crawl_report.json", report)
    atomic_write_json(output_dir / "expansion_report.json", {"sources": expansion, "generated_at": now_iso()})
    atomic_write_json(output_dir / "crawl_state.json", {"version": 1, "config_hash": report["config_hash"], "updated_at": now_iso()})
    with (output_dir / "failures.jsonl").open("w", encoding="utf-8") as f:
        for item in failures:
            append_jsonl(f, item)
    for name in ("records.jsonl", "crawl.log"):
        (output_dir / name).touch()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if failures else 0


def load_retry_urls(output_dir: Path, failure_type: Optional[str]) -> List[Tuple[Optional[str], str, int]]:
    path = output_dir / "failures.jsonl"
    result = []
    if not path.exists():
        return result
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if failure_type and obj.get("error_type") != failure_type:
                continue
            if obj.get("url"):
                result.append((obj.get("source_id"), obj["url"], int(obj.get("depth") or 0)))
    return result


def crawl(config_path: Path, output_dir: Path, args: argparse.Namespace) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "crawl.log"
    log_level = getattr(logging, args.log_level.upper(), logging.INFO)
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
    file_handler = logging.FileHandler(str(log_path), encoding="utf-8")
    file_handler.setLevel(log_level)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root_logger.addHandler(file_handler)
    config_text = config_path.read_text(encoding="utf-8")
    config_hash = sha256_text(config_text)
    config = json.loads(config_text)
    sources = make_sources(config)
    if args.source_id:
        wanted = set(args.source_id)
        sources = [s for s in sources if s.source_id in wanted]
    source_by_id = {s.source_id: s for s in sources}
    state_path = output_dir / "crawl_state.json"
    state = load_state(state_path) if args.resume else load_state(Path("__missing_state__.json"))
    if state.get("config_hash") and state.get("config_hash") != config_hash:
        logging.warning("Config hash changed: previous=%s current=%s", state.get("config_hash"), config_hash)
    successful_urls: Set[str] = set(state.get("successful_urls") or [])
    failed_urls: Set[str] = set(state.get("failed_urls") or [])
    seen_hashes: Set[str] = set(state.get("content_hashes") or [])
    record_ids: Set[str] = set(state.get("record_ids") or []) | jsonl_record_ids(output_dir / "records.jsonl")
    run_id = "run_%s" % now_iso()
    failures_count = 0
    records_count = 0
    skipped_count = 0
    expansion_entries: List[Dict[str, Any]] = []
    source_stats: Dict[str, Counter] = defaultdict(Counter)
    last_request_by_domain: Dict[str, float] = {}

    retry_items = load_retry_urls(output_dir, args.failure_type) if args.retry_failures else []
    progress = ProgressReporter(enabled=not args.no_progress, every=args.progress_every)
    progress.emit(
        "crawl start: sources=%s output=%s expansion_mode=%s resume=%s retry_failures=%s"
        % (len(sources), output_dir, args.expansion_mode, args.resume, args.retry_failures),
        force=True,
    )

    with (output_dir / "records.jsonl").open("a", encoding="utf-8") as records_f, (
        output_dir / "failures.jsonl"
    ).open("a", encoding="utf-8") as failures_f:
        for source_index, source in enumerate(sources, start=1):
            progress.emit(
                "source %s/%s start: %s domain=%s max_pages=%s max_depth=%s"
                % (source_index, len(sources), source.source_id, source.domain, source.max_pages(args.max_pages_per_seed), source.max_depth(args.max_depth)),
                force=True,
            )
            for err in source.regex_errors:
                append_jsonl(failures_f, err)
                failures_count += 1
            seed_check = seed_self_check(source)
            if not seed_check["ok"]:
                append_jsonl(failures_f, failure_record(source, source.seed_url, 0, seed_check["reason"], extra=seed_check))
                failures_count += 1
                continue
            session = make_session(source.user_agent())
            robots = RobotCache(
                source.user_agent(),
                bool(source.policy("respect_robots_txt", True)),
                int(args.timeout),
            )
            queue: Deque[Tuple[str, Optional[str], int, str]] = deque()
            if args.retry_failures:
                for sid, url, depth in retry_items:
                    if sid is None or sid == source.source_id:
                        queue.append((url, None, depth, ""))
            else:
                queue.append((source.seed_url, None, 0, ""))
            source_seen: Set[str] = set()
            candidate_count = 0
            accepted_count = 0
            accepted_links_for_patterns: List[Dict[str, str]] = []
            cannot_expand_reason = None

            while queue and source_stats[source.source_id]["pages_attempted"] < source.max_pages(args.max_pages_per_seed):
                url, parent, depth, link_context = queue.popleft()
                normalized = normalize_url(url)
                if not normalized:
                    append_jsonl(failures_f, failure_record(source, url, depth, "invalid_url"))
                    failures_count += 1
                    continue
                if normalized in source_seen:
                    skipped_count += 1
                    continue
                source_seen.add(normalized)
                progress.page_event(
                    source_stats[source.source_id]["pages_attempted"] + 1,
                    "checking source=%s attempted_next=%s/%s depth=%s queue=%s url=%s"
                    % (source.source_id, source_stats[source.source_id]["pages_attempted"] + 1, source.max_pages(args.max_pages_per_seed), depth, len(queue), normalized[:180]),
                )
                rid = make_record_id(source.source_id, normalized)
                if args.resume and not args.force_refresh and normalized in successful_urls:
                    skipped_count += 1
                    progress.page_event(
                        source_stats[source.source_id]["pages_attempted"],
                        "skip already_successful source=%s skipped=%s url=%s" % (source.source_id, skipped_count, normalized[:180]),
                        force=True,
                    )
                    continue
                if rid in record_ids and args.force_refresh:
                    logging.info("force-refresh fetched URL would reuse record_id, keeping JSONL unique: %s", rid)
                validation = url_allowed_by_config(normalized, source, "strict" if depth == 0 else args.expansion_mode, link_context)
                if depth == 0 and not validation["allowed"] and validation["reason"] == "not_allowed_by_strict_patterns":
                    validation["reason"] = "seed_not_allowed_by_config"
                if not validation["allowed"]:
                    append_jsonl(failures_f, failure_record(source, normalized, depth, validation["reason"], extra={"validation": validation}))
                    failures_count += 1
                    failed_urls.add(normalized)
                    progress.emit(
                        "blocked source=%s reason=%s failures=%s url=%s" % (source.source_id, validation["reason"], failures_count, normalized[:180]),
                        force=True,
                    )
                    continue
                allowed_by_robots, robots_info = robots.can_fetch(normalized)
                validation["robots"] = robots_info
                if not allowed_by_robots:
                    append_jsonl(failures_f, failure_record(source, normalized, depth, "robots_denied", extra={"validation": validation}))
                    failures_count += 1
                    failed_urls.add(normalized)
                    progress.emit(
                        "blocked source=%s reason=robots_denied failures=%s url=%s" % (source.source_id, failures_count, normalized[:180]),
                        force=True,
                    )
                    continue
                domain = urlparse(normalized).netloc
                elapsed = time.time() - last_request_by_domain.get(domain, 0)
                wait = source.interval(args.request_interval) - elapsed
                if wait > 0:
                    time.sleep(wait)
                last_request_by_domain[domain] = time.time()
                source_stats[source.source_id]["pages_attempted"] += 1
                progress.page_event(
                    source_stats[source.source_id]["pages_attempted"],
                    "fetching source=%s attempted=%s/%s records=%s failures=%s url=%s"
                    % (source.source_id, source_stats[source.source_id]["pages_attempted"], source.max_pages(args.max_pages_per_seed), records_count, failures_count, normalized[:180]),
                )
                fetch_result = request_with_retries(session, normalized, int(args.timeout), int(args.max_retries))
                if not fetch_result.get("ok"):
                    append_jsonl(
                        failures_f,
                        failure_record(
                            source,
                            normalized,
                            depth,
                            fetch_result.get("error_type") or "network_error",
                            fetch_result.get("status_code"),
                            int(fetch_result.get("retry_count") or 0),
                            fetch_result.get("exception"),
                        ),
                    )
                    failures_count += 1
                    failed_urls.add(normalized)
                    progress.emit(
                        "failed source=%s error=%s status=%s retries=%s failures=%s url=%s"
                        % (source.source_id, fetch_result.get("error_type") or "network_error", fetch_result.get("status_code"), fetch_result.get("retry_count") or 0, failures_count, normalized[:180]),
                        force=True,
                    )
                    continue
                final_url = normalize_url(fetch_result.get("final_url") or normalized)
                if final_url and not url_domain_allowed(final_url, source.domain, source.seed_url, source.follow_external()):
                    append_jsonl(failures_f, failure_record(source, normalized, depth, "redirect_out_of_scope"))
                    failures_count += 1
                    failed_urls.add(normalized)
                    progress.emit(
                        "failed source=%s error=redirect_out_of_scope failures=%s url=%s final=%s"
                        % (source.source_id, failures_count, normalized[:120], (final_url or "")[:120]),
                        force=True,
                    )
                    continue
                kind = content_type_kind(final_url or normalized, fetch_result.get("headers") or {})
                try:
                    if kind == "pdf":
                        extraction = extract_pdf(fetch_result["body"])
                        links: List[Dict[str, str]] = []
                    elif kind == "html":
                        extraction = extract_html(fetch_result["body"], final_url or normalized, fetch_result.get("headers") or {})
                        links = extraction.pop("links")
                    else:
                        append_jsonl(failures_f, failure_record(source, normalized, depth, "unsupported_content_type", fetch_result.get("status_code")))
                        failures_count += 1
                        failed_urls.add(normalized)
                        progress.emit(
                            "failed source=%s error=unsupported_content_type kind=%s failures=%s url=%s"
                            % (source.source_id, kind, failures_count, normalized[:180]),
                            force=True,
                        )
                        continue
                except Exception as exc:
                    append_jsonl(failures_f, failure_record(source, normalized, depth, "%s_extraction_failed" % kind, fetch_result.get("status_code"), exception=repr(exc)))
                    failures_count += 1
                    failed_urls.add(normalized)
                    progress.emit(
                        "failed source=%s error=%s_extraction_failed failures=%s url=%s exception=%s"
                        % (source.source_id, kind, failures_count, normalized[:140], repr(exc)[:120]),
                        force=True,
                    )
                    continue
                if "login_or_captcha_or_paywall_detected" in (extraction.get("quality_flags") or []):
                    append_jsonl(failures_f, failure_record(source, normalized, depth, "login_or_captcha_page", fetch_result.get("status_code")))
                record = build_record(
                    run_id,
                    source,
                    source.seed_url,
                    normalized,
                    parent,
                    depth,
                    fetch_result,
                    extraction,
                    links,
                    validation,
                    seen_hashes,
                )
                if record["record_id"] not in record_ids:
                    append_jsonl(records_f, record)
                    record_ids.add(record["record_id"])
                    records_count += 1
                else:
                    skipped_count += 1
                if record["content_hash"]:
                    seen_hashes.add(record["content_hash"])
                progress.page_event(
                    source_stats[source.source_id]["pages_attempted"],
                    "saved source=%s status=%s records=%s skipped=%s failures=%s text_length=%s score=%s queue=%s url=%s"
                    % (source.source_id, record.get("http_status"), records_count, skipped_count, failures_count, record.get("text_length"), record.get("relevance_score"), len(queue), normalized[:140]),
                    force=True,
                )
                successful_urls.add(normalized)
                failed_urls.discard(normalized)
                source_stats[source.source_id]["pages_succeeded"] += 1
                if depth < source.max_depth(args.max_depth):
                    strict_accepts = 0
                    controlled_accepts = 0
                    for link in links:
                        candidate_count += 1
                        context = " ".join([link.get("anchor_text", ""), link.get("context", "")])
                        strict_validation = url_allowed_by_config(link["url"], source, "strict", context)
                        validation_mode = strict_validation
                        if strict_validation["allowed"]:
                            strict_accepts += 1
                        elif args.expansion_mode == "controlled":
                            validation_mode = url_allowed_by_config(link["url"], source, "controlled", context)
                            if validation_mode["allowed"]:
                                controlled_accepts += 1
                        if validation_mode["allowed"]:
                            nurl = validation_mode["normalized_url"]
                            if nurl and nurl not in source_seen:
                                queue.append((nurl, normalized, depth + 1, context))
                                accepted_count += 1
                                accepted_links_for_patterns.append(link)
                    if candidate_count and accepted_count == 0:
                        cannot_expand_reason = "no_links_passed_%s_expansion" % args.expansion_mode
                    elif not links:
                        cannot_expand_reason = "no_links_discovered"
                    logging.info(
                        "source=%s depth=%s strict_accepts=%s controlled_accepts=%s",
                        source.source_id,
                        depth,
                        strict_accepts,
                        controlled_accepts,
                    )
                state.update(
                    {
                        "version": 1,
                        "config_hash": config_hash,
                        "successful_urls": sorted(successful_urls),
                        "failed_urls": sorted(failed_urls),
                        "content_hashes": sorted(seen_hashes),
                        "record_ids": sorted(record_ids),
                        "updated_at": now_iso(),
                    }
                )
                atomic_write_json(state_path, state)
            expansion_entries.append(
                {
                    "source_id": source.source_id,
                    "seed_url": source.seed_url,
                    "seed_check": seed_check,
                    "expansion_mode": args.expansion_mode,
                    "candidate_count": candidate_count,
                    "accepted_candidate_count": accepted_count,
                    "derived_patterns": classify_link_patterns(accepted_links_for_patterns).get("inferred_path_patterns", []),
                    "cannot_expand_reason": cannot_expand_reason,
                    "pages_attempted": source_stats[source.source_id]["pages_attempted"],
                    "pages_succeeded": source_stats[source.source_id]["pages_succeeded"],
                }
            )
            progress.emit(
                "source %s/%s done: %s attempted=%s succeeded=%s candidates=%s accepted=%s records=%s failures=%s skipped=%s"
                % (source_index, len(sources), source.source_id, source_stats[source.source_id]["pages_attempted"], source_stats[source.source_id]["pages_succeeded"], candidate_count, accepted_count, records_count, failures_count, skipped_count),
                force=True,
            )

    report = {
        "run_id": run_id,
        "config_path": str(config_path),
        "config_hash": config_hash,
        "dry_run": False,
        "source_count": len(sources),
        "records_written": records_count,
        "failures_written": failures_count,
        "skipped": skipped_count,
        "source_stats": {k: dict(v) for k, v in source_stats.items()},
        "generated_at": now_iso(),
    }
    atomic_write_json(output_dir / "crawl_report.json", report)
    atomic_write_json(output_dir / "expansion_report.json", {"sources": expansion_entries, "generated_at": now_iso()})
    state.update(
        {
            "version": 1,
            "config_hash": config_hash,
            "successful_urls": sorted(successful_urls),
            "failed_urls": sorted(failed_urls),
            "content_hashes": sorted(seen_hashes),
            "record_ids": sorted(record_ids),
            "updated_at": now_iso(),
        }
    )
    atomic_write_json(state_path, state)
    progress.emit(
        "crawl complete: records=%s failures=%s skipped=%s report=%s"
        % (records_count, failures_count, skipped_count, output_dir / "crawl_report.json"),
        force=True,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Virtual currency illegal case targeted crawler")
    parser.add_argument("--config", default=None)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--source-id", action="append", default=[])
    parser.add_argument("--resume", dest="resume", action="store_true", default=True)
    parser.add_argument("--no-resume", dest="resume", action="store_false")
    parser.add_argument("--max-pages-per-seed", type=int, default=None)
    parser.add_argument("--max-depth", type=int, default=None)
    parser.add_argument("--request-interval", type=float, default=None)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--relevance-threshold", type=int, default=0)
    parser.add_argument("--expansion-mode", choices=["strict", "controlled"], default="controlled")
    parser.add_argument("--enable-js", action="store_true", default=False)
    parser.add_argument("--save-pdf", dest="save_pdf", action="store_true", default=True)
    parser.add_argument("--no-save-pdf", dest="save_pdf", action="store_false")
    parser.add_argument("--force-refresh", action="store_true", default=False)
    parser.add_argument("--retry-failures", action="store_true", default=False)
    parser.add_argument("--failure-type", default=None)
    parser.add_argument("--dry-run", action="store_true", default=False)
    parser.add_argument("--no-progress", action="store_true", default=False, help="Disable console progress messages")
    parser.add_argument("--progress-every", type=int, default=1, help="Print routine progress every N attempted pages")
    parser.add_argument("--log-level", default="INFO")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    config_path = find_config_path(args.config)
    output_dir = Path(args.output_dir)
    if args.enable_js:
        # The current env.txt does not list Playwright. Keep the CLI flag explicit
        # and non-fatal, but record unsupported JS pages as failures during crawl.
        try:
            import playwright  # noqa: F401
        except Exception:
            print("warning: --enable-js requested but Playwright is not available; JS fallback disabled", file=sys.stderr)
    if args.dry_run:
        return dry_run(config_path, output_dir, args)
    return crawl(config_path, output_dir, args)


if __name__ == "__main__":
    raise SystemExit(main())
