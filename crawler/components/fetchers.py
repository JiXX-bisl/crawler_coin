# -*- coding: utf-8 -*-
import time
from typing import Dict

import requests


class FetchResult(dict):
    pass


class HttpFetcher:
    def __init__(self, user_agent: str, timeout: int, max_retries: int):
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml,application/pdf,text/plain,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            }
        )

    def fetch(self, url: str) -> Dict:
        retry_status = {429, 500, 502, 503, 504}
        attempt = 0
        while attempt <= self.max_retries:
            try:
                response = self.session.get(url, timeout=self.timeout, allow_redirects=True)
                if response.status_code in retry_status and attempt < self.max_retries:
                    attempt += 1
                    time.sleep(min(2 ** attempt, 8))
                    continue
                return FetchResult(
                    ok=200 <= response.status_code < 300,
                    status_code=response.status_code,
                    final_url=response.url,
                    headers=dict(response.headers),
                    body=response.content,
                    retry_count=attempt,
                    error_type=None if 200 <= response.status_code < 300 else "http_error",
                )
            except requests.Timeout as exc:
                if attempt >= self.max_retries:
                    return FetchResult(ok=False, error_type="network_timeout", exception=repr(exc), retry_count=attempt)
                attempt += 1
                time.sleep(min(2 ** attempt, 8))
            except requests.RequestException as exc:
                if attempt >= self.max_retries:
                    return FetchResult(ok=False, error_type="network_error", exception=repr(exc), retry_count=attempt)
                attempt += 1
                time.sleep(min(2 ** attempt, 8))
        return FetchResult(ok=False, error_type="unknown_fetch_error", retry_count=attempt)
