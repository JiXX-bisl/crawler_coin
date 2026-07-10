# -*- coding: utf-8 -*-
"""
virtual_currency_static_crawler.py

多币种虚拟货币知识库静态爬虫。

本脚本是在 bitcoin_static_crawler.py 的基础上改造而来，适配结构化后的
virtual_currency_seed_urls.json。它支持：
1. 读取结构化 URL 种子 JSON；
2. 根据 crawl_enabled 过滤不适合静态爬取的页面；
3. 支持 html / api_doc / pdf / github_markdown / github_repo / html_crawl；
4. 保留 coin、chain、asset_type、knowledge_domain、knowledge_category、
   source_priority、topic_tags 等元数据；
5. 输出 records.jsonl、records.xlsx、knowledge.db、crawl_report.txt；
6. 支持按 coin / priority / knowledge_domain 过滤，便于分批爬取。

适用环境：Python 3.8+
推荐从项目根目录运行：
python scripts/virtual_currency_static_crawler.py --config configs/virtual_currency_seed_urls.json --out data/virtual_currency_raw --delay 1.0
"""

import argparse
import hashlib
import json
import re
import sqlite3
import time
import traceback
from collections import Counter, deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urldefrag, urljoin, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# -----------------------------
# 基础工具函数
# -----------------------------

def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


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
    return [x.strip() for x in re.split(r"[,;|，；、]+", text) if x.strip()]


def csv_filter_set(value: str) -> Optional[set]:
    items = as_list(value)
    return set(x.upper() for x in items) if items else None


def normalize_url(url: Any, base_url: Optional[str] = None) -> Optional[str]:
    """
    将配置文件或 BeautifulSoup 中得到的 URL 统一规范化。

    BeautifulSoup 的 tag.get("href") 可能被类型检查器推断为 str/list/None，
    因此这里做运行时兼容。
    """
    if url is None:
        return None
    if isinstance(url, (list, tuple)):
        if not url:
            return None
        url = url[0]
    if not isinstance(url, str):
        url = str(url)
    url = url.strip()
    if not url:
        return None
    if url.startswith(("mailto:", "javascript:", "tel:", "data:")):
        return None
    if base_url:
        url = urljoin(base_url, url)
    url, _ = urldefrag(url)
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return None
    # 统一去掉末尾斜杠，根路径保留
    if parsed.path not in ("", "/") and url.endswith("/"):
        url = url[:-1]
    return url


def normalize_text(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\xa0", " ").replace("\u200b", "")
    text = re.sub(r"[ \t\x0b\x0c]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    lines = []
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


def safe_name_from_url(url: str, suffix: str = ".txt", prefix: str = "") -> str:
    parsed = urlparse(url)
    raw = (parsed.netloc + parsed.path).strip("/") or "index"
    raw = re.sub(r"[^0-9a-zA-Z._-]+", "_", raw)
    digest = hashlib.sha256(url.encode("utf-8", errors="ignore")).hexdigest()[:12]
    if len(raw) > 120:
        raw = raw[:120]
    if not suffix.startswith("."):
        suffix = "." + suffix
    if prefix:
        prefix = re.sub(r"[^0-9a-zA-Z._-]+", "_", prefix.strip())[:60] + "__"
    return f"{prefix}{raw}_{digest}{suffix}"


def safe_name_from_path(path: str, suffix: str = ".txt", prefix: str = "") -> str:
    raw = path.strip("/").replace("/", "__") or "index"
    raw = re.sub(r"[^0-9a-zA-Z._-]+", "_", raw)
    digest = hashlib.sha256(path.encode("utf-8", errors="ignore")).hexdigest()[:12]
    if len(raw) > 140:
        raw = raw[-140:]
    if not suffix.startswith("."):
        suffix = "." + suffix
    if prefix:
        prefix = re.sub(r"[^0-9a-zA-Z._-]+", "_", prefix.strip())[:60] + "__"
    return f"{prefix}{raw}_{digest}{suffix}"


def ensure_dirs(out_dir: Path) -> Dict[str, Path]:
    dirs = {
        "raw_html": out_dir / "raw_html",
        "raw_files": out_dir / "raw_files",
        "raw_text": out_dir / "raw_text",
        "clean_text": out_dir / "clean_text",
        "reports": out_dir,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    for p in dirs.values():
        p.mkdir(parents=True, exist_ok=True)
    return dirs


def make_session(timeout: int, user_agent: str) -> requests.Session:
    session = requests.Session()
    retries = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "HEAD"]),
    )
    adapter = HTTPAdapter(max_retries=retries, pool_connections=10, pool_maxsize=10)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update(
        {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf,text/plain,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
    )
    session.request_timeout = timeout  # type: ignore[attr-defined]
    return session


def fetch(session: requests.Session, url: str, binary: bool = False) -> Tuple[Optional[bytes], Optional[str], Optional[str]]:
    """返回: (bytes内容, content_type, error)"""
    try:
        timeout = getattr(session, "request_timeout", 60)
        resp = session.get(url, timeout=timeout)
        resp.raise_for_status()
        content_type = resp.headers.get("Content-Type", "")
        if binary:
            return resp.content, content_type, None

        encoding = resp.encoding or resp.apparent_encoding or "utf-8"
        return resp.text.encode(encoding, errors="ignore"), content_type, None
    except Exception as exc:
        return None, None, repr(exc)


# -----------------------------
# HTML 处理
# -----------------------------

def html_to_text_and_links(html: str, base_url: str) -> Tuple[str, str, List[str]]:
    soup = BeautifulSoup(html, "lxml")

    for tag in soup(["script", "style", "noscript", "svg", "canvas", "form", "iframe"]):
        tag.decompose()
    for tag in soup(["header", "footer", "nav", "aside"]):
        tag.decompose()

    title = ""
    h1 = soup.find("h1")
    if h1:
        title = normalize_text(h1.get_text(" ", strip=True))
    if not title and soup.title:
        title = normalize_text(soup.title.get_text(" ", strip=True))

    candidates = []
    selectors = [
        "main",
        "article",
        "[role=main]",
        "div.document",
        "div.body",
        "div.content",
        "div.container",
        "div.markdown-body",
        "section.content",
        "#content",
        "#__next",
        "#root",
    ]
    for selector in selectors:
        found = soup.select_one(selector)
        if found:
            candidates.append(found)
    root = candidates[0] if candidates else soup.body or soup

    # 保留表格文本
    for table in root.find_all("table"):
        rows = []
        for tr in table.find_all("tr"):
            cells = [normalize_text(td.get_text(" ", strip=True)) for td in tr.find_all(["th", "td"])]
            cells = [c for c in cells if c]
            if cells:
                rows.append(" | ".join(cells))
        if rows:
            table.replace_with("\n" + "\n".join(rows) + "\n")

    content = normalize_text(root.get_text("\n", strip=True))

    links = []
    for a in soup.find_all("a", href=True):
        href = a.get("href")
        if not isinstance(href, str):
            continue
        u = normalize_url(href, base_url)
        if u:
            links.append(u)
    links = sorted(set(links))
    return title, content, links


def html_allowed_for_crawl(url: str, seed_url: str, source: Dict[str, Any]) -> bool:
    u = normalize_url(url)
    if not u:
        return False
    parsed = urlparse(u)
    seed_parsed = urlparse(seed_url)

    if source.get("same_domain", True) and parsed.netloc != seed_parsed.netloc:
        return False

    path_prefix = source.get("path_prefix")
    if path_prefix and not parsed.path.startswith(path_prefix):
        return False

    lowered = parsed.path.lower()
    blocked_ext = (
        ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".zip", ".gz", ".tar",
        ".css", ".js", ".ico", ".mp4", ".mp3", ".xml", ".json", ".csv", ".xlsx",
    )
    if lowered.endswith(blocked_ext):
        return False

    return True


# -----------------------------
# GitHub URL 处理
# -----------------------------

def parse_github_repo_url(url: str) -> Optional[Tuple[str, str]]:
    parsed = urlparse(url)
    if parsed.netloc.lower() != "github.com":
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        return None
    owner, repo = parts[0], parts[1]
    if owner in ("orgs", "marketplace", "topics") or repo in ("issues", "pulls"):
        return None
    return owner, repo


def github_blob_to_raw(url: str) -> Optional[Dict[str, str]]:
    """
    支持：
    https://github.com/{owner}/{repo}/blob/{branch}/{path}
    https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}
    """
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    parts = [p for p in parsed.path.split("/") if p]

    if host == "github.com" and len(parts) >= 5 and parts[2] == "blob":
        owner, repo, branch = parts[0], parts[1], parts[3]
        path = "/".join(parts[4:])
        raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
        page_url = f"https://github.com/{owner}/{repo}/blob/{branch}/{path}"
        return {
            "owner": owner,
            "repo": repo,
            "branch": branch,
            "path": path,
            "raw_url": raw_url,
            "page_url": page_url,
        }

    if host == "raw.githubusercontent.com" and len(parts) >= 4:
        owner, repo, branch = parts[0], parts[1], parts[2]
        path = "/".join(parts[3:])
        page_url = f"https://github.com/{owner}/{repo}/blob/{branch}/{path}"
        return {
            "owner": owner,
            "repo": repo,
            "branch": branch,
            "path": path,
            "raw_url": url,
            "page_url": page_url,
        }

    return None


def get_default_github_branch(session: requests.Session, owner: str, repo: str, delay: float) -> str:
    api_url = f"https://api.github.com/repos/{owner}/{repo}"
    data, _content_type, error = fetch(session, api_url, binary=False)
    time.sleep(delay)
    if data is not None:
        try:
            payload = json.loads(data.decode("utf-8", errors="ignore"))
            branch = payload.get("default_branch")
            if branch:
                return str(branch)
        except Exception:
            pass
    return "master"


# -----------------------------
# 文档记录构造与保存
# -----------------------------

METADATA_FIELDS = [
    "id",
    "level_1",
    "level_2",
    "coin",
    "chain",
    "asset_type",
    "knowledge_domain",
    "knowledge_category",
    "source_name",
    "source_authority",
    "source_priority",
    "crawl_type",
    "crawl_enabled",
    "topic_tags",
    "topic_count",
    "normalized_url",
    "notes",
]


def source_id(source: Dict[str, Any]) -> str:
    sid = str(source.get("id") or source.get("source_id") or "").strip()
    if sid:
        return sid
    url = str(source.get("normalized_url") or source.get("url") or source.get("name") or "")
    return "SRC_" + sha256_text(url)[:12]


def source_url(source: Dict[str, Any]) -> str:
    return str(source.get("normalized_url") or source.get("url") or "").strip()


def source_name(source: Dict[str, Any]) -> str:
    return str(source.get("source_name") or source.get("name") or source_id(source)).strip()


def source_crawl_type(source: Dict[str, Any]) -> str:
    return str(source.get("crawl_type") or source.get("type") or "html").strip()


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text or "", encoding="utf-8", errors="ignore")


def write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data or b"")


def make_doc_id(source: Dict[str, Any], url: str, suffix: str = "") -> str:
    sid = source_id(source)
    if not suffix:
        # 固定 URL 单文档时直接使用 source id，便于追溯
        if normalize_url(url) == normalize_url(source_url(source)):
            return sid
        suffix = sha256_text(url)[:10]
    suffix = re.sub(r"[^0-9a-zA-Z._-]+", "_", suffix)[:80]
    return f"{sid}__{suffix}"


def make_record(
    source: Dict[str, Any],
    url: str,
    title: str,
    content: str,
    raw_path: Optional[Path],
    text_path: Optional[Path],
    status: str = "ok",
    error: str = "",
    extra: Optional[Dict[str, Any]] = None,
    doc_id_suffix: str = "",
) -> Dict[str, Any]:
    content = content or ""
    rec: Dict[str, Any] = {
        "doc_id": make_doc_id(source, url, doc_id_suffix),
        "source_id": source_id(source),
        "seed_url": source_url(source),
        "url": url,
        "title": title or "",
        "content": content,
        "content_hash": sha256_text(content),
        "char_count": len(content),
        "raw_path": str(raw_path) if raw_path else "",
        "text_path": str(text_path) if text_path else "",
        "crawl_time": now_str(),
        "status": status,
        "error": error or "",
    }

    # 写入结构化种子元数据
    for key in METADATA_FIELDS:
        if key in source:
            rec[key] = source.get(key)

    # 兼容原脚本字段名
    rec["source_name"] = source_name(source)
    rec["source_group"] = str(source.get("knowledge_domain") or source.get("group") or "")
    rec["source_type"] = source_crawl_type(source)
    rec["crawl_type"] = source_crawl_type(source)

    if "topic_tags" in rec and isinstance(rec["topic_tags"], str):
        rec["topic_tags"] = as_list(rec["topic_tags"])

    if extra:
        rec.update(extra)

    return rec


def json_dumps_for_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, dict, tuple)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def save_outputs(
    records: List[Dict[str, Any]],
    out_dir: Path,
    disabled_sources: List[Dict[str, Any]],
    selected_sources: List[Dict[str, Any]],
) -> None:
    # 按 doc_id 去重，保留最后一次采集结果；github_repo 会生成多条 doc_id
    dedup: Dict[str, Dict[str, Any]] = {}
    for rec in records:
        dedup[rec.get("doc_id") or rec.get("url") or sha256_text(json.dumps(rec, ensure_ascii=False))] = rec
    records = list(dedup.values())

    jsonl_path = out_dir / "records.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # Excel 单元格长度有限，content 只保留预览
    rows = []
    for rec in records:
        r = dict(rec)
        content = r.get("content", "") or ""
        r["content_preview"] = content[:5000]
        r.pop("content", None)
        for k, v in list(r.items()):
            if isinstance(v, (list, dict, tuple)):
                r[k] = json_dumps_for_cell(v)
        rows.append(r)

    xlsx_path = out_dir / "records.xlsx"
    if rows:
        pd.DataFrame(rows).to_excel(xlsx_path, index=False)

    failed_jsonl = out_dir / "failed_records.jsonl"
    with failed_jsonl.open("w", encoding="utf-8") as f:
        for rec in records:
            if rec.get("status") not in ("ok", "partial"):
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    disabled_path = out_dir / "disabled_sources.jsonl"
    with disabled_path.open("w", encoding="utf-8") as f:
        for src in disabled_sources:
            f.write(json.dumps(src, ensure_ascii=False) + "\n")

    db_path = out_dir / "knowledge.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS documents (
                doc_id TEXT PRIMARY KEY,
                source_id TEXT,
                seed_url TEXT,
                url TEXT,
                title TEXT,
                content TEXT,
                content_hash TEXT,
                char_count INTEGER,
                raw_path TEXT,
                text_path TEXT,
                crawl_time TEXT,
                status TEXT,
                error TEXT,

                level_1 TEXT,
                level_2 TEXT,
                coin TEXT,
                chain TEXT,
                asset_type TEXT,
                knowledge_domain TEXT,
                knowledge_category TEXT,
                source_name TEXT,
                source_authority TEXT,
                source_priority TEXT,
                source_type TEXT,
                crawl_type TEXT,
                crawl_enabled TEXT,
                topic_tags TEXT,
                topic_count INTEGER,
                normalized_url TEXT,
                notes TEXT,

                metadata_json TEXT
            )
            """
        )
        for rec in records:
            conn.execute(
                """
                INSERT OR REPLACE INTO documents (
                    doc_id, source_id, seed_url, url, title, content, content_hash,
                    char_count, raw_path, text_path, crawl_time, status, error,
                    level_1, level_2, coin, chain, asset_type, knowledge_domain,
                    knowledge_category, source_name, source_authority, source_priority,
                    source_type, crawl_type, crawl_enabled, topic_tags, topic_count,
                    normalized_url, notes, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rec.get("doc_id"),
                    rec.get("source_id"),
                    rec.get("seed_url"),
                    rec.get("url"),
                    rec.get("title"),
                    rec.get("content"),
                    rec.get("content_hash"),
                    rec.get("char_count"),
                    rec.get("raw_path"),
                    rec.get("text_path"),
                    rec.get("crawl_time"),
                    rec.get("status"),
                    rec.get("error"),
                    rec.get("level_1"),
                    rec.get("level_2"),
                    rec.get("coin"),
                    rec.get("chain"),
                    rec.get("asset_type"),
                    rec.get("knowledge_domain"),
                    rec.get("knowledge_category"),
                    rec.get("source_name"),
                    rec.get("source_authority"),
                    rec.get("source_priority"),
                    rec.get("source_type"),
                    rec.get("crawl_type"),
                    str(rec.get("crawl_enabled")),
                    json_dumps_for_cell(rec.get("topic_tags")),
                    rec.get("topic_count") if isinstance(rec.get("topic_count"), int) else None,
                    rec.get("normalized_url"),
                    rec.get("notes"),
                    json.dumps(rec, ensure_ascii=False),
                ),
            )
        conn.commit()
    finally:
        conn.close()

    status_counter = Counter(str(r.get("status")) for r in records)
    domain_counter = Counter(str(r.get("knowledge_domain", "")) for r in records)
    coin_counter = Counter(str(r.get("coin", "")) for r in records)
    crawl_type_counter = Counter(str(r.get("crawl_type", "")) for r in records)
    priority_counter = Counter(str(r.get("source_priority", "")) for r in records)

    ok_count = status_counter.get("ok", 0)
    partial_count = status_counter.get("partial", 0)
    fail_count = sum(v for k, v in status_counter.items() if k not in ("ok", "partial"))

    report = [
        f"crawl_time: {now_str()}",
        f"total_seed_sources: {len(selected_sources) + len(disabled_sources)}",
        f"enabled_sources_selected: {len(selected_sources)}",
        f"disabled_sources_skipped: {len(disabled_sources)}",
        f"total_records: {len(records)}",
        f"ok_records: {ok_count}",
        f"partial_records: {partial_count}",
        f"failed_records: {fail_count}",
        f"jsonl: {jsonl_path}",
        f"xlsx: {xlsx_path}",
        f"sqlite: {db_path}",
        f"failed_jsonl: {failed_jsonl}",
        f"disabled_sources_jsonl: {disabled_path}",
        "",
        "status_summary:",
    ]
    for k, v in status_counter.most_common():
        report.append(f"- {k}: {v}")

    report.append("")
    report.append("priority_summary:")
    for k, v in priority_counter.most_common():
        report.append(f"- {k}: {v}")

    report.append("")
    report.append("crawl_type_summary:")
    for k, v in crawl_type_counter.most_common():
        report.append(f"- {k}: {v}")

    report.append("")
    report.append("knowledge_domain_summary:")
    for k, v in domain_counter.most_common():
        report.append(f"- {k}: {v}")

    report.append("")
    report.append("coin_summary:")
    for k, v in coin_counter.most_common():
        report.append(f"- {k}: {v}")

    report.append("")
    report.append("failed_items:")
    for r in records:
        if r.get("status") not in ("ok", "partial"):
            report.append(f"- {r.get('source_id')} | {r.get('url')} | {r.get('error')}")

    report.append("")
    report.append("disabled_items:")
    for src in disabled_sources:
        report.append(f"- {source_id(src)} | {source_url(src)} | {src.get('notes', '')}")

    (out_dir / "crawl_report.txt").write_text("\n".join(report), encoding="utf-8")


# -----------------------------
# 各来源采集器
# -----------------------------

def crawl_pdf(session: requests.Session, source: Dict[str, Any], dirs: Dict[str, Path], delay: float) -> List[Dict[str, Any]]:
    url = source_url(source)
    print(f"[PDF] {source_id(source)} {url}")

    data, content_type, error = fetch(session, url, binary=True)
    time.sleep(delay)

    if data is None:
        return [make_record(source, url, source_name(source), "", None, None, "failed", error or "fetch failed")]

    raw_path = dirs["raw_files"] / safe_name_from_url(url, ".pdf", prefix=source_id(source))
    write_bytes(raw_path, data)

    text = ""
    pdf_error = ""
    try:
        from pypdf import PdfReader  # 可选依赖

        reader = PdfReader(str(raw_path))
        pages = []
        for idx, page in enumerate(reader.pages):
            try:
                page_text = page.extract_text() or ""
                if page_text.strip():
                    pages.append(f"[Page {idx + 1}]\n{page_text}")
            except Exception as exc:
                pages.append(f"[Page {idx + 1}]\n[PDF page extract failed: {repr(exc)}]")
        text = normalize_text("\n\n".join(pages))
    except Exception as exc:
        pdf_error = "PDF saved, but text extraction failed. Install pypdf if needed. " + repr(exc)

    title = source_name(source)
    text_path = dirs["clean_text"] / safe_name_from_url(url, ".txt", prefix=source_id(source))
    write_text(text_path, text)

    status = "ok" if text else "partial"
    return [
        make_record(
            source,
            url,
            title,
            text,
            raw_path,
            text_path,
            status=status,
            error=pdf_error,
            extra={
                "content_type": content_type or "application/pdf",
                "file_sha256": sha256_bytes(data),
            },
        )
    ]


def crawl_html_exact(session: requests.Session, source: Dict[str, Any], dirs: Dict[str, Path], delay: float) -> List[Dict[str, Any]]:
    url = source_url(source)
    print(f"[HTML] {source_id(source)} {url}")

    data, content_type, error = fetch(session, url, binary=False)
    time.sleep(delay)

    if data is None:
        return [make_record(source, url, source_name(source), "", None, None, "failed", error or "fetch failed")]

    html = data.decode("utf-8", errors="ignore")
    raw_path = dirs["raw_html"] / safe_name_from_url(url, ".html", prefix=source_id(source))
    write_text(raw_path, html)

    title, content, links = html_to_text_and_links(html, url)
    if not title:
        title = source_name(source)

    text_path = dirs["clean_text"] / safe_name_from_url(url, ".txt", prefix=source_id(source))
    write_text(text_path, content)

    status = "ok" if content else "partial"
    return [
        make_record(
            source,
            url,
            title,
            content,
            raw_path,
            text_path,
            status=status,
            error="" if content else "empty content after html cleaning",
            extra={
                "content_type": content_type or "text/html",
                "link_count": len(links),
            },
        )
    ]


def crawl_html_site(session: requests.Session, source: Dict[str, Any], dirs: Dict[str, Path], delay: float) -> List[Dict[str, Any]]:
    seed_url = source_url(source)
    max_pages = int(source.get("max_pages", 50))
    max_depth = int(source.get("max_depth", 1))
    print(f"[HTML_CRAWL] {source_id(source)} seed={seed_url}, max_pages={max_pages}, max_depth={max_depth}")

    seen = set()
    queue = deque([(seed_url, 0)])
    records: List[Dict[str, Any]] = []

    while queue and len(seen) < max_pages:
        url, depth = queue.popleft()
        url = normalize_url(url)
        if not url or url in seen:
            continue
        if not html_allowed_for_crawl(url, seed_url, source):
            continue
        seen.add(url)

        print(f"  - [{len(seen)}/{max_pages}] depth={depth} {url}")
        data, content_type, error = fetch(session, url, binary=False)
        time.sleep(delay)

        if data is None:
            records.append(make_record(source, url, source_name(source), "", None, None, "failed", error or "fetch failed"))
            continue

        html = data.decode("utf-8", errors="ignore")
        raw_path = dirs["raw_html"] / safe_name_from_url(url, ".html", prefix=source_id(source))
        write_text(raw_path, html)

        title, content, links = html_to_text_and_links(html, url)
        if not title:
            title = source_name(source)

        text_path = dirs["clean_text"] / safe_name_from_url(url, ".txt", prefix=source_id(source))
        write_text(text_path, content)

        records.append(
            make_record(
                source,
                url,
                title,
                content,
                raw_path,
                text_path,
                status="ok" if content else "partial",
                error="" if content else "empty content after html cleaning",
                extra={
                    "content_type": content_type or "text/html",
                    "depth": depth,
                    "link_count": len(links),
                },
                doc_id_suffix=sha256_text(url)[:10],
            )
        )

        if depth < max_depth:
            for link in links:
                if link not in seen and html_allowed_for_crawl(link, seed_url, source):
                    queue.append((link, depth + 1))

    return records


def crawl_github_markdown(session: requests.Session, source: Dict[str, Any], dirs: Dict[str, Path], delay: float) -> List[Dict[str, Any]]:
    url = source_url(source)
    parsed = github_blob_to_raw(url)

    if not parsed:
        # 不是标准 GitHub blob/raw URL 时，退回普通 HTML 抓取
        print(f"[GITHUB_MD->HTML] {source_id(source)} {url}")
        return crawl_html_exact(session, source, dirs, delay)

    raw_url = parsed["raw_url"]
    page_url = parsed["page_url"]
    path = parsed["path"]

    print(f"[GITHUB_MD] {source_id(source)} {path}")
    data, content_type, error = fetch(session, raw_url, binary=False)
    time.sleep(delay)

    if data is None:
        return [make_record(source, page_url, path, "", None, None, "failed", error or "raw file fetch failed")]

    text = data.decode("utf-8", errors="ignore")
    raw_path = dirs["raw_text"] / safe_name_from_path(path, ".txt", prefix=source_id(source))
    write_text(raw_path, text)

    clean = normalize_text(text)
    text_path = dirs["clean_text"] / safe_name_from_path(path, ".txt", prefix=source_id(source))
    write_text(text_path, clean)

    return [
        make_record(
            source,
            page_url,
            path,
            clean,
            raw_path,
            text_path,
            status="ok" if clean else "partial",
            error="" if clean else "empty text file",
            extra={
                "github_owner": parsed["owner"],
                "github_repo": parsed["repo"],
                "github_branch": parsed["branch"],
                "github_path": path,
                "raw_url": raw_url,
                "content_type": content_type or "text/plain",
            },
        )
    ]


def crawl_github_repo(
    session: requests.Session,
    source: Dict[str, Any],
    dirs: Dict[str, Path],
    delay: float,
    default_max_files: int,
    default_extensions: Iterable[str],
) -> List[Dict[str, Any]]:
    url = source_url(source)
    parsed_repo = parse_github_repo_url(url)

    if not parsed_repo:
        # 例如 https://github.com/flashbots 这类组织页，不是单一仓库，退回 HTML
        print(f"[GITHUB_REPO->HTML] {source_id(source)} {url}")
        return crawl_html_exact(session, source, dirs, delay)

    owner, repo = parsed_repo
    branch = str(source.get("branch") or "").strip()
    if not branch:
        branch = get_default_github_branch(session, owner, repo, delay)

    extensions = tuple(str(x).lower() for x in source.get("extensions", default_extensions))
    max_files = int(source.get("max_files", default_max_files))

    # BIPs 这类标准库建议完整一些
    if owner.lower() == "bitcoin" and repo.lower() == "bips" and "max_files" not in source:
        max_files = max(max_files, 500)

    api_url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
    print(f"[GITHUB_REPO] {source_id(source)} {owner}/{repo}@{branch}, max_files={max_files}")

    data, content_type, error = fetch(session, api_url, binary=False)
    time.sleep(delay)

    if data is None:
        return [make_record(source, url, source_name(source), "", None, None, "failed", error or "GitHub API fetch failed")]

    try:
        payload = json.loads(data.decode("utf-8", errors="ignore"))
    except Exception as exc:
        return [make_record(source, url, source_name(source), "", None, None, "failed", repr(exc))]

    tree = payload.get("tree", [])
    files = []
    for item in tree:
        if item.get("type") != "blob":
            continue
        path = str(item.get("path", ""))
        if path.lower().endswith(extensions):
            files.append(path)
    files = files[:max_files]

    records: List[Dict[str, Any]] = []
    for i, path in enumerate(files, 1):
        raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
        page_url = f"https://github.com/{owner}/{repo}/blob/{branch}/{path}"
        print(f"  - [{i}/{len(files)}] {path}")

        raw_data, raw_content_type, raw_error = fetch(session, raw_url, binary=False)
        time.sleep(delay)

        if raw_data is None:
            records.append(make_record(source, page_url, path, "", None, None, "failed", raw_error or "raw file fetch failed", doc_id_suffix=sha256_text(path)[:10]))
            continue

        text = raw_data.decode("utf-8", errors="ignore")
        raw_path = dirs["raw_text"] / safe_name_from_path(path, ".txt", prefix=source_id(source))
        write_text(raw_path, text)

        clean = normalize_text(text)
        text_path = dirs["clean_text"] / safe_name_from_path(path, ".txt", prefix=source_id(source))
        write_text(text_path, clean)

        records.append(
            make_record(
                source,
                page_url,
                path,
                clean,
                raw_path,
                text_path,
                status="ok" if clean else "partial",
                error="" if clean else "empty text file",
                extra={
                    "github_owner": owner,
                    "github_repo": repo,
                    "github_branch": branch,
                    "github_path": path,
                    "raw_url": raw_url,
                    "content_type": raw_content_type or "text/plain",
                },
                doc_id_suffix=sha256_text(path)[:10],
            )
        )

    return records


# -----------------------------
# 配置加载与过滤
# -----------------------------

def load_sources(config_path: str) -> List[Dict[str, Any]]:
    p = Path(config_path)
    if not p.exists():
        raise FileNotFoundError(f"config not found: {config_path}")

    payload = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        sources = payload
    elif isinstance(payload, dict) and isinstance(payload.get("sources"), list):
        sources = payload["sources"]
    else:
        raise ValueError("config must be a JSON list or {'sources': [...]} object")

    fixed: List[Dict[str, Any]] = []
    for src in sources:
        if not isinstance(src, dict):
            continue
        src = dict(src)
        if "normalized_url" not in src or not src.get("normalized_url"):
            src["normalized_url"] = normalize_url(src.get("url")) or str(src.get("url") or "")
        if "crawl_type" not in src or not src.get("crawl_type"):
            src["crawl_type"] = src.get("type") or "html"
        if "source_name" not in src or not src.get("source_name"):
            src["source_name"] = src.get("name") or source_id(src)
        fixed.append(src)

    return fixed


def filter_sources(args: argparse.Namespace, sources: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    only_priority = csv_filter_set(args.only_priority)
    only_domain = csv_filter_set(args.only_domain)
    only_coin = csv_filter_set(args.only_coin)
    only_level1 = csv_filter_set(args.only_level1)
    only_crawl_type = csv_filter_set(args.only_crawl_type)

    selected: List[Dict[str, Any]] = []
    disabled: List[Dict[str, Any]] = []

    for src in sources:
        enabled = bool(src.get("crawl_enabled", True))
        if not enabled and not args.include_disabled:
            disabled.append(src)
            continue

        if only_priority and str(src.get("source_priority", "")).upper() not in only_priority:
            continue
        if only_domain and str(src.get("knowledge_domain", "")).upper() not in only_domain:
            continue
        if only_coin and str(src.get("coin", "")).upper() not in only_coin:
            continue
        if only_level1 and str(src.get("level_1", "")).upper() not in only_level1:
            continue
        if only_crawl_type and source_crawl_type(src).upper() not in only_crawl_type:
            continue

        selected.append(src)

    if args.limit and args.limit > 0:
        selected = selected[: args.limit]

    return selected, disabled


def write_dry_run_report(out_dir: Path, selected: List[Dict[str, Any]], disabled: List[Dict[str, Any]]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "dry_run_sources.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for src in selected:
            f.write(json.dumps(src, ensure_ascii=False) + "\n")

    report = [
        f"dry_run_time: {now_str()}",
        f"selected_sources: {len(selected)}",
        f"disabled_sources_skipped: {len(disabled)}",
        "",
        "selected_summary_by_priority:",
    ]

    for k, v in Counter(str(s.get("source_priority", "")) for s in selected).most_common():
        report.append(f"- {k}: {v}")

    report.append("")
    report.append("selected_summary_by_crawl_type:")
    for k, v in Counter(source_crawl_type(s) for s in selected).most_common():
        report.append(f"- {k}: {v}")

    report.append("")
    report.append("selected_summary_by_coin:")
    for k, v in Counter(str(s.get("coin", "")) for s in selected).most_common():
        report.append(f"- {k}: {v}")

    (out_dir / "dry_run_report.txt").write_text("\n".join(report), encoding="utf-8")
    print(f"[DRY-RUN] selected_sources={len(selected)}, disabled_sources_skipped={len(disabled)}")
    print(f"[DRY-RUN] report: {out_dir / 'dry_run_report.txt'}")


# -----------------------------
# 主程序
# -----------------------------

def run(args: argparse.Namespace) -> None:
    out_dir = Path(args.out).resolve()
    dirs = ensure_dirs(out_dir)

    sources = load_sources(args.config)
    selected_sources, disabled_sources = filter_sources(args, sources)

    if args.dry_run:
        write_dry_run_report(out_dir, selected_sources, disabled_sources)
        return

    session = make_session(timeout=args.timeout, user_agent=args.user_agent)

    default_extensions = as_list(args.github_extensions)
    if not default_extensions:
        default_extensions = [".md", ".txt", ".rst", ".mediawiki", ".adoc"]

    all_records: List[Dict[str, Any]] = []

    print(f"[INFO] loaded_sources={len(sources)}")
    print(f"[INFO] selected_enabled_sources={len(selected_sources)}")
    print(f"[INFO] disabled_sources_skipped={len(disabled_sources)}")
    print(f"[INFO] out_dir={out_dir}")

    for idx, source in enumerate(selected_sources, 1):
        ctype = source_crawl_type(source)
        url = source_url(source)

        print(f"\n[SOURCE {idx}/{len(selected_sources)}] {source_id(source)} | {ctype} | {url}")

        try:
            if ctype == "pdf":
                records = crawl_pdf(session, source, dirs, args.delay)
            elif ctype in ("html", "api_doc", "interactive_html", "html_exact"):
                records = crawl_html_exact(session, source, dirs, args.delay)
            elif ctype == "html_crawl":
                records = crawl_html_site(session, source, dirs, args.delay)
            elif ctype == "github_markdown":
                records = crawl_github_markdown(session, source, dirs, args.delay)
            elif ctype == "github_repo":
                records = crawl_github_repo(
                    session,
                    source,
                    dirs,
                    args.delay,
                    default_max_files=args.github_max_files,
                    default_extensions=default_extensions,
                )
            else:
                records = [
                    make_record(
                        source,
                        url or source_id(source),
                        source_name(source),
                        "",
                        None,
                        None,
                        "failed",
                        f"unknown crawl_type: {ctype}",
                    )
                ]

            all_records.extend(records)

        except KeyboardInterrupt:
            print("\n[STOP] interrupted by user")
            break
        except Exception as exc:
            print(f"[ERROR] source failed: {source_id(source)} | {repr(exc)}")
            traceback.print_exc()
            all_records.append(
                make_record(
                    source,
                    url or source_id(source),
                    source_name(source),
                    "",
                    None,
                    None,
                    "failed",
                    repr(exc),
                )
            )

    save_outputs(all_records, out_dir, disabled_sources=disabled_sources, selected_sources=selected_sources)

    print("\n[DONE]")
    print(f"output_dir: {out_dir}")
    print(f"records: {len(all_records)}")
    print(f"jsonl: {out_dir / 'records.jsonl'}")
    print(f"xlsx: {out_dir / 'records.xlsx'}")
    print(f"sqlite: {out_dir / 'knowledge.db'}")
    print(f"report: {out_dir / 'crawl_report.txt'}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Multi-coin virtual currency static crawler")

    parser.add_argument(
        "--config",
        default="configs/virtual_currency_seed_urls.json",
        help="结构化 URL 种子 JSON 路径。",
    )
    parser.add_argument(
        "--out",
        default="data/virtual_currency_raw",
        help="输出目录。",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="每次请求后的等待秒数，建议 >= 0.5。",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="单次请求超时时间，单位秒。",
    )
    parser.add_argument(
        "--user-agent",
        default="Mozilla/5.0 (compatible; VirtualCurrencyKnowledgeCrawler/0.2; +https://example.local)",
        help="请求 User-Agent。",
    )

    parser.add_argument(
        "--include-disabled",
        action="store_true",
        help="默认跳过 crawl_enabled=false 的来源；设置该参数后也会尝试爬取。",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只检查和输出将要爬取的来源，不真正发起网络请求。",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="只爬取前 N 个筛选后的来源，0 表示不限制。",
    )

    parser.add_argument(
        "--only-priority",
        default="",
        help="按 source_priority 过滤，例如 A 或 A,B。",
    )
    parser.add_argument(
        "--only-domain",
        default="",
        help="按 knowledge_domain 过滤，例如 basic_concept,asset_profile。",
    )
    parser.add_argument(
        "--only-coin",
        default="",
        help="按 coin 过滤，例如 BTC,ETH,USDT。注意 N/A 也可作为过滤值。",
    )
    parser.add_argument(
        "--only-level1",
        default="",
        help="按 level_1 过滤，例如 01_基础概念,02_主流币种。",
    )
    parser.add_argument(
        "--only-crawl-type",
        default="",
        help="按 crawl_type 过滤，例如 html,pdf,github_markdown。",
    )

    parser.add_argument(
        "--github-max-files",
        type=int,
        default=200,
        help="github_repo 默认最多抓取的文本文件数。bitcoin/bips 会自动提高到至少 500。",
    )
    parser.add_argument(
        "--github-extensions",
        default=".md,.txt,.rst,.mediawiki,.adoc",
        help="github_repo 抓取的文件扩展名，逗号分隔。",
    )

    return parser


if __name__ == "__main__":
    run(build_parser().parse_args())
