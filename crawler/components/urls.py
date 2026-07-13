# -*- coding: utf-8 -*-
import re
from urllib.parse import parse_qsl, urlencode, urldefrag, urljoin, urlparse, urlunparse

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "spm", "from", "session", "timestamp", "fbclid", "gclid",
}

UNSUPPORTED_EXTENSIONS = {
    ".7z", ".avi", ".bmp", ".css", ".csv", ".doc", ".docx", ".exe",
    ".flac", ".gif", ".gz", ".ico", ".jpeg", ".jpg", ".js", ".m4a",
    ".mov", ".mp3", ".mp4", ".png", ".rar", ".svg", ".tar", ".ttf",
    ".wav", ".webm", ".webp", ".woff", ".woff2", ".xls", ".xlsx", ".zip",
}
TERMINAL_EXTENSIONS = {".json", ".markdown", ".md", ".txt", ".xml"}
GITHUB_HOSTS = {"github.com", "gist.github.com", "raw.githubusercontent.com"}


def normalize_url(url, base_url=None):
    if url is None:
        return None
    url = str(url).strip()
    if not url or re.match(r"^(mailto|javascript|tel|data):", url, re.I):
        return None
    if base_url:
        url = urljoin(base_url, url)
    url, _ = urldefrag(url)
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ("http", "https"):
        return None
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    netloc = host
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = "%s:%s" % (host, port)
    path = parsed.path or "/"
    params = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k.lower() not in TRACKING_PARAMS]
    query = urlencode(sorted(params), doseq=True)
    return urlunparse((scheme, netloc, path, "", query, ""))


def host_matches(host, domain):
    domain = str(domain or "").lower().lstrip(".").rstrip(".")
    return bool(domain) and (host == domain or host.endswith("." + domain))


def same_domain(url, seed_url):
    host = (urlparse(url).hostname or "").lower()
    seed_host = (urlparse(seed_url).hostname or "").lower()
    return host_matches(host, seed_host)


def domain_allowed(url, seed_url, rules):
    host = (urlparse(url).hostname or "").lower()
    if rules.same_domain and same_domain(url, seed_url):
        return True
    return any(host_matches(host, domain) for domain in rules.allowed_domains)


def classify_url(url):
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").lower()
    if host in GITHUB_HOSTS or host.endswith(".github.com"):
        return "github"
    if path.endswith(".pdf"):
        return "pdf"
    if any(path.endswith(ext) for ext in UNSUPPORTED_EXTENSIONS):
        return "unsupported"
    if any(path.endswith(ext) for ext in TERMINAL_EXTENSIONS):
        return "terminal"
    return "html"


def compile_patterns(patterns):
    compiled = []
    errors = []
    for pattern in patterns or []:
        try:
            compiled.append(re.compile(str(pattern), re.I))
        except re.error as exc:
            errors.append({"pattern": pattern, "error": str(exc)})
    return compiled, errors


def allowed_by_rules(url, seed_url, rules):
    normalized = normalize_url(url)
    if not normalized:
        return False, "invalid_url"
    if rules.same_domain and not domain_allowed(normalized, seed_url, rules):
        return False, "domain_not_allowed"
    if not rules.same_domain and rules.allowed_domains:
        host = (urlparse(normalized).hostname or "").lower()
        if not any(host_matches(host, domain) for domain in rules.allowed_domains):
            return False, "domain_not_allowed"
    if rules.path_prefix and not urlparse(normalized).path.startswith(rules.path_prefix):
        return False, "path_prefix_miss"
    allow, allow_errors = compile_patterns(rules.allow_patterns)
    deny, deny_errors = compile_patterns(rules.deny_patterns)
    if allow_errors or deny_errors:
        return False, "regex_error"
    if any(pattern.search(normalized) for pattern in deny):
        return False, "deny_regex"
    if allow and not any(pattern.search(normalized) for pattern in allow):
        return False, "allow_regex_miss"
    return True, "allowed"
