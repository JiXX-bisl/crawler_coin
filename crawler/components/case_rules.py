# -*- coding: utf-8 -*-
import re

CASE_NUMBER_PATTERNS = [
    re.compile(r"[\uff08(]\d{4}[\uff09)][\u4e00-\u9fff]{1,8}\d{0,8}[\u4e00-\u9fff]{0,12}\d{1,10}\u53f7"),
    re.compile(r"\d{4}[\u4e00-\u9fff]{1,8}\d{0,8}[\u4e00-\u9fff]{0,12}\d{1,10}\u53f7"),
]
ORGANIZATION_PATTERN = re.compile(r"[\u4e00-\u9fff]{2,20}(?:\u4eba\u6c11\u6cd5\u9662|\u4eba\u6c11\u68c0\u5bdf\u9662|\u516c\u5b89\u5c40|\u516c\u5b89\u673a\u5173|\u53cd\u6d17\u94b1\u4e2d\u5fc3)")
DATE_PATTERN = re.compile(r"(?:19|20)\d{2}[\u5e74\-/.]\d{1,2}[\u6708\-/.]\d{1,2}(?:\u65e5)?")
AMOUNT_PATTERN = re.compile(r"(?:\u4eba\u6c11\u5e01|RMB|USD|\uffe5|\$)?\s*\d[\d,]*(?:\.\d+)?\s*(?:\u4ebf\u5143|\u4e07\u5143|\u5143|\u4e07|\u4ebf|USDT|BTC|ETH|\u6cf0\u8fbe\u5e01|\u6bd4\u7279\u5e01)", re.I)
LEGAL_ARTICLE_PATTERN = re.compile(r"\u300a[^\u300b]{2,60}\u300b\u7b2c\d{1,4}\u6761(?:\u4e4b\u4e00|\u4e4b\u4e8c|\u7b2c\d{1,3}\u6b3e)?")

CRIME_TYPES = [
    "\u6d17\u94b1\u7f6a", "\u8bc8\u9a97\u7f6a", "\u96c6\u8d44\u8bc8\u9a97\u7f6a", "\u975e\u6cd5\u7ecf\u8425\u7f6a", "\u7ec4\u7ec7\u3001\u9886\u5bfc\u4f20\u9500\u6d3b\u52a8\u7f6a",
    "\u5e2e\u52a9\u4fe1\u606f\u7f51\u7edc\u72af\u7f6a\u6d3b\u52a8\u7f6a", "\u6392\u9970\u3001\u9690\u7792\u72af\u7f6a\u6240\u5f97\u3001\u72af\u7f6a\u6240\u5f97\u6536\u76ca\u7f6a", "\u5f00\u8bbe\u8d4c\u573a\u7f6a",
]
KEYWORDS = ["\u865a\u62df\u8d27\u5e01", "\u6570\u5b57\u8d27\u5e01", "\u6bd4\u7279\u5e01", "USDT", "NFT", "\u533a\u5757\u94fe", "\u6570\u5b57\u85cf\u54c1"]


def _unique(values):
    return list(dict.fromkeys(value.strip() for value in values if value and value.strip()))


def _organizations(text):
    excluded = ("\u5e2e\u52a9", "\u534f\u52a9", "\u9003\u907f", "\u5e76\u5728", "\u4ee5\u6b64", "\u5e94\u53ca\u65f6", "\u672c\u6848", "\u5145\u5206", "\u5f70\u663e", "\u94f6\u884c\u5361", "\u6d89\u8bc8", "\u6d41\u5165", "\u72af\u7f6a\u5206\u5b50")
    organizations = []
    for value in ORGANIZATION_PATTERN.findall(text):
        value = value.strip()
        if any(token in value for token in excluded):
            continue
        if value.startswith(("\u5411", "\u88ab", "\u5728")):
            value = value[1:]
        organizations.append(value)
    return _unique(organizations)


def extract_case_facts(text):
    text = text or ""
    case_numbers = []
    for pattern in CASE_NUMBER_PATTERNS:
        case_numbers.extend(pattern.findall(text))
    return {
        "case_numbers": _unique(case_numbers),
        "organizations": _organizations(text),
        "crime_types": [crime for crime in CRIME_TYPES if crime in text],
        "amounts": _unique(AMOUNT_PATTERN.findall(text)),
        "dates": _unique(DATE_PATTERN.findall(text)),
        "legal_articles": _unique(LEGAL_ARTICLE_PATTERN.findall(text)),
        "keywords": [keyword for keyword in KEYWORDS if keyword.lower() in text.lower()],
    }
