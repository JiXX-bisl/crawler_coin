# -*- coding: utf-8 -*-
import re


def clean_text(text):
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    return "\n".join(lines)


def extract_html(parsed, extraction):
    soup = parsed["soup"]
    for tag in soup(["script", "style", "noscript", "svg", "canvas", "iframe", "form"]):
        tag.decompose()

    strategy = extraction.strategy
    content_node = None
    if strategy == "css_selector":
        selector = extraction.fields.get("content")
        if isinstance(selector, dict):
            selector = selector.get("selector")
        if selector:
            content_node = soup.select_one(selector)
    if content_node is None:
        candidates = soup.select("article, main, [role=main], .article, .content, #content") or [soup.body or soup]
        content_node = max(candidates, key=lambda node: len(clean_text(node.get_text("\n", strip=True))))

    title = ""
    meta_title = soup.select_one('meta[property="og:title"], meta[name="twitter:title"]')
    if meta_title and meta_title.get("content"):
        title = clean_text(meta_title.get("content"))
    title_node = content_node.find("h1") or soup.select_one("article h1, main h1, [role=main] h1") or soup.find("h1")
    if not title and title_node:
        title = clean_text(title_node.get_text(" ", strip=True))
    if not title and soup.title:
        title = clean_text(soup.title.get_text(" ", strip=True))
    content = clean_text(content_node.get_text("\n", strip=True))
    return {"title": title, "content": content, "content_blocks": [{"order": 0, "type": "text", "text": content}]}


def extract_pdf(parsed):
    content = clean_text(parsed.get("text") or "")
    return {
        "title": None,
        "content": content,
        "content_blocks": [
            {"order": i, "type": "pdf_page", "page_number": page["page_number"], "text": clean_text(page["text"])}
            for i, page in enumerate(parsed.get("pages") or [])
        ],
        "pdf_pages": parsed.get("pages") or [],
    }
