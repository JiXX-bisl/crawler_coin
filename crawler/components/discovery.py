# -*- coding: utf-8 -*-
import re

from .urls import classify_url, normalize_url

NAVIGATION_KEYWORDS = {
    "about", "account", "careers", "comment", "contact", "cookie", "dashboard", "feedback",
    "jobs", "login", "logout", "myworkbench", "newsletter", "privacy", "register", "search",
    "share", "signin", "signup", "subscribe", "terms", "user-center", "workbench",
    "\u5173\u4e8e", "\u516c\u544a", "\u5206\u4eab", "\u53cd\u9988", "\u8054\u7cfb\u6211\u4eec", "\u767b\u5f55", "\u6ce8\u518c", "\u8ba2\u9605", "\u8bc4\u8bba", "\u9690\u79c1", "\u7528\u6237\u4e2d\u5fc3", "\u5de5\u4f5c\u53f0", "\u670d\u52a1\u6307\u5357", "\u5e74\u5ea6\u62a5\u8868",
}
CASE_PATH_PATTERN = re.compile(
    r"/(?:zixun/xiangqing|xwfbh/dxal|spp/xwfbh/dxal|xyfxts|yasf|dxal|case|cases|judgment|judgements)/|/C\d{3}/\d{10,}|/Show/\d+|/content/[A-Za-z0-9_-]+\.html$",
    re.I,
)
CASE_TEXT_PATTERN = re.compile(
    r"\u5178\u578b\u6848\u4f8b|\u57fa\u672c\u6848\u60c5|\u88c1\u5224\u7ed3\u679c|\u5224\u51b3\u4e66|\u88c1\u5224\u6587\u4e66|\u6848\u4ef6|\u6848\u53f7|\u5ba3\u5224|\u8d77\u8bc9|\u5ba1\u5224",
    re.I,
)
DETAIL_PATTERN = re.compile(r"/(article|articles|case|cases|content|detail|details|doc|docs|guides?|post|posts|report|reports|topic|topics|xiangqing)/|\d{4,}", re.I)
PAGINATION_PATTERN = re.compile(r"([?&](page|p|paged|page_no|page_num)=\d+)|/(page|list)/?\d*/?$", re.I)
PAGINATION_TEXT = {"next", "previous", "prev", "next page", "\u4e0a\u4e00\u9875", "\u4e0b\u4e00\u9875"}


def _clean(value):
    return " ".join((value or "").split())


def _nearby_context(anchor):
    for parent in list(anchor.parents)[:3]:
        if len(parent.find_all("a")) <= 2:
            text = _clean(parent.get_text(" ", strip=True))
            if text:
                return text[:240]
    return _clean(anchor.get_text(" ", strip=True))[:240]


def _is_pagination(url, anchor):
    text = _clean(anchor).lower()
    return bool(PAGINATION_PATTERN.search(url) or text in PAGINATION_TEXT or re.fullmatch(r"\d{1,3}", text))


def _case_detail_signal(url, anchor_text, title, aria_label):
    direct = " ".join([url, anchor_text, title, aria_label])
    return bool(CASE_PATH_PATTERN.search(url) or CASE_TEXT_PATTERN.search(direct))


def discover_html_links(parsed, base_url):
    soup = parsed["soup"]
    links = []
    for anchor in soup.find_all("a", href=True):
        url = normalize_url(anchor.get("href"), base_url)
        if not url:
            continue
        anchor_text = _clean(anchor.get_text(" ", strip=True))
        title = _clean(anchor.get("title") or "")
        aria_label = _clean(anchor.get("aria-label") or "")
        links.append(
            {
                "url": url,
                "anchor_text": anchor_text,
                "title": title,
                "aria_label": aria_label,
                "context": _nearby_context(anchor),
                "rel": anchor.get("rel") or [],
                "node_type": classify_url(url),
                "is_pagination": _is_pagination(url, anchor_text),
                "is_detail": bool(DETAIL_PATTERN.search(url)),
                "is_case_detail": _case_detail_signal(url, anchor_text, title, aria_label),
            }
        )
    return links


def link_has_negative_signal(link, negative_keywords=None):
    direct = " ".join([link.get("url", ""), link.get("anchor_text", ""), link.get("title", ""), link.get("aria_label", "")]).lower()
    keywords = set(NAVIGATION_KEYWORDS)
    keywords.update(str(value).lower() for value in (negative_keywords or []) if value)
    return any(keyword in direct for keyword in keywords)


def _has_keyword(text, keywords):
    return any(keyword and keyword in text for keyword in keywords)


def link_score(link, discovery, topic_keywords=None, multi_signal=False):
    if link_has_negative_signal(link, discovery.negative_keywords):
        return 0.0
    keywords = [str(value).lower() for value in (topic_keywords or discovery.positive_keywords) if value]
    if not multi_signal:
        text = " ".join([link.get("url", ""), link.get("anchor_text", ""), link.get("title", ""), link.get("aria_label", ""), link.get("context", "")]).lower()
        if _has_keyword(text, keywords):
            return 1.0
        if link.get("is_pagination"):
            return 0.8
        if link.get("node_type") == "pdf" or link.get("is_detail"):
            return 0.6
        return 0.0

    url_text = link.get("url", "").lower()
    label_text = " ".join([link.get("anchor_text", ""), link.get("title", ""), link.get("aria_label", "")]).lower()
    context_text = link.get("context", "").lower()
    score = 0.0
    if _has_keyword(url_text, keywords):
        score += 0.45
    if _has_keyword(label_text, keywords):
        score += 0.35
    if _has_keyword(context_text, keywords):
        score += 0.20
    if link.get("is_case_detail"):
        score += 0.30
    if link.get("is_pagination"):
        score = max(score, 0.70)
    return min(round(score, 2), 1.0)
