# -*- coding: utf-8 -*-
from io import BytesIO

from bs4 import BeautifulSoup

try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None


def detect_parser(url, headers, configured):
    if configured and configured != "auto":
        return configured
    ctype = (headers or {}).get("Content-Type", "").lower()
    if "pdf" in ctype or url.lower().split("?")[0].endswith(".pdf"):
        return "pdf"
    return "html"


def parse_html(body, headers=None):
    encoding = "utf-8"
    ctype = (headers or {}).get("Content-Type", "")
    if "charset=" in ctype.lower():
        encoding = ctype.split("charset=", 1)[1].split(";", 1)[0].strip()
    try:
        text = body.decode(encoding, errors="replace")
    except LookupError:
        text = body.decode("utf-8", errors="replace")
        encoding = "utf-8"
    return {"parser": "html", "encoding": encoding, "soup": BeautifulSoup(text, "lxml")}


def parse_pdf(body):
    pages = []
    if PdfReader is None:
        return {"parser": "pdf", "pages": pages, "text": "", "quality_flags": ["pdf_reader_unavailable"]}
    try:
        reader = PdfReader(BytesIO(body))
        for index, page in enumerate(reader.pages, start=1):
            pages.append({"page_number": index, "text": page.extract_text() or ""})
    except Exception as exc:
        return {"parser": "pdf", "pages": pages, "text": "", "quality_flags": ["pdf_parse_failed"], "error": repr(exc)}
    return {"parser": "pdf", "pages": pages, "text": "\n".join(p["text"] for p in pages), "quality_flags": []}
