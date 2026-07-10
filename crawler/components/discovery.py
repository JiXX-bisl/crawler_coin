# -*- coding: utf-8 -*-
from .urls import normalize_url


def discover_html_links(parsed, base_url):
    soup = parsed["soup"]
    links = []
    for a in soup.find_all("a", href=True):
        url = normalize_url(a.get("href"), base_url)
        if not url:
            continue
        anchor = a.get_text(" ", strip=True)
        context = anchor
        parent = a.parent
        if parent and len(parent.find_all("a")) <= 1:
            context = parent.get_text(" ", strip=True)
        links.append({"url": url, "anchor_text": anchor, "context": context})
    return links


def link_score(link, discovery):
    text = " ".join([link.get("url", ""), link.get("anchor_text", ""), link.get("context", "")]).lower()
    if any(k.lower() in text for k in discovery.negative_keywords):
        return 0.0
    positives = discovery.positive_keywords
    if not positives:
        return 1.0
    hits = sum(1 for k in positives if k.lower() in text)
    return hits / max(len(positives), 1)
