# -*- coding: utf-8 -*-
import re
from urllib.parse import parse_qsl, urlencode, urldefrag, urljoin, urlparse, urlunparse

TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "spm",
    "from",
    "session",
    "timestamp",
    "fbclid",
    "gclid",
}


def normalize_url(url, base_url=None):
    if url is None:
        return None
    url = str(url).strip()
    if not url:
        return None
    if re.match(r"^(mailto|javascript|tel|data):", url, re.I):
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
    port = parsed.port
    netloc = host
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = "%s:%s" % (host, port)
    path = parsed.path or "/"
    params = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k.lower() not in TRACKING_PARAMS]
    query = urlencode(sorted(params), doseq=True)
    return urlunparse((scheme, netloc, path, "", query, ""))


def same_domain(url, seed_url):
    host = (urlparse(url).hostname or "").lower()
    seed_host = (urlparse(seed_url).hostname or "").lower()
    return host == seed_host or host.endswith("." + seed_host)


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
    if rules.same_domain and not same_domain(normalized, seed_url):
        return False, "domain_not_allowed"
    if rules.path_prefix and not urlparse(normalized).path.startswith(rules.path_prefix):
        return False, "path_prefix_miss"
    allow, allow_errors = compile_patterns(rules.allow_patterns)
    deny, deny_errors = compile_patterns(rules.deny_patterns)
    if allow_errors or deny_errors:
        return False, "regex_error"
    if any(p.search(normalized) for p in deny):
        return False, "deny_regex"
    if allow and not any(p.search(normalized) for p in allow):
        return False, "allow_regex_miss"
    return True, "allowed"
