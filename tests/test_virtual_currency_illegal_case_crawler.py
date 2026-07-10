# -*- coding: utf-8 -*-
import importlib.util
import json
import uuid
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "virtual_currency_illegal_case_crawler.py"
spec = importlib.util.spec_from_file_location("virtual_currency_illegal_case_crawler", MODULE_PATH)
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


def zh(s):
    return s.encode("ascii").decode("unicode_escape")


@pytest.fixture
def local_tmp_path():
    base = Path("data") / "virtual_currency_illegal_case_crawl" / "test_tmp"
    path = base / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=False)
    return path


def make_source(**overrides):
    raw = {
        "id": "source_test",
        "source_name": "test court",
        "domain": "court.gov.cn",
        "seed_url": "https://www.court.gov.cn/zixun/xiangqing/436301.html",
        "allow_url_patterns": [r"/zixun/xiangqing/436301\.html"],
        "deny_url_patterns": [r"/login", r"\.(jpg|png|css|js)(\?.*)?$"],
        "crawl_depth": 1,
    }
    raw.update(overrides)
    allow, allow_errors = c.compile_patterns(c.as_list(raw.get("allow_url_patterns")), "allow", raw["id"])
    deny, deny_errors = c.compile_patterns(c.as_list(raw.get("deny_url_patterns")), "deny", raw["id"])
    return c.SourceRuntime(raw, {"max_default_crawl_depth": 3, "max_pages_per_seed": 10, "retain_pdf_documents": True}, allow, deny, allow_errors + deny_errors)


def test_config_field_compatibility_and_seed_self_check():
    source = make_source()
    assert source.source_id == "source_test"
    assert source.max_depth() == 1
    assert source.max_pages() == 10
    assert c.seed_self_check(source)["ok"] is True
    bad = make_source(allow_url_patterns=[r"/other/"])
    check = c.seed_self_check(bad)
    assert check["ok"] is False
    assert check["reason"] == "seed_not_allowed_by_config"


def test_normalize_url_removes_tracking_sorts_query_and_default_port():
    url = c.normalize_url("HTTPS://Example.COM:443/a/../b/?utm_source=x&b=2&a=1#frag")
    assert url == "https://example.com/a/../b?a=1&b=2"
    assert c.normalize_url("javascript:alert(1)") is None


def test_allow_deny_pdf_and_domain_boundary():
    source = make_source(allow_url_patterns=[r"/zixun/xiangqing/\d+\.html", r"\.pdf$"])
    allowed = c.url_allowed_by_config("https://www.court.gov.cn/zixun/xiangqing/123.html", source)
    assert allowed["allowed"] is True
    denied = c.url_allowed_by_config("https://www.court.gov.cn/login", source)
    assert denied["reason"] == "deny_pattern"
    offsite = c.url_allowed_by_config("https://evilcourt.gov.cn/zixun/xiangqing/123.html", source)
    assert offsite["reason"] == "domain_not_allowed"
    pdf = c.url_allowed_by_config("https://www.court.gov.cn/files/a.pdf", source, "controlled", zh(r"\u6848\u4ef6\u9644\u4ef6"))
    assert pdf["allowed"] is True


def test_strict_and_controlled_expansion():
    source = make_source(allow_url_patterns=[r"/zixun/xiangqing/436301\.html"])
    ctx = zh(r"\u865a\u62df\u8d27\u5e01\u6848\u4ef6")
    strict = c.url_allowed_by_config("https://www.court.gov.cn/zixun/xiangqing/999999.html", source, "strict", ctx)
    controlled = c.url_allowed_by_config("https://www.court.gov.cn/zixun/xiangqing/999999.html", source, "controlled", ctx)
    assert strict["allowed"] is False
    assert controlled["allowed"] is True


def test_robots_disabled_and_denied(monkeypatch):
    rc = c.RobotCache("ua", respect=False)
    assert rc.can_fetch("https://example.com/a")[0] is True

    class FakeRobot:
        def set_url(self, url):
            self.url = url

        def read(self):
            return None

        def can_fetch(self, ua, url):
            return False

    monkeypatch.setattr(c.robotparser, "RobotFileParser", FakeRobot)
    rc = c.RobotCache("ua", respect=True)
    allowed, info = rc.can_fetch("https://example.com/private")
    assert allowed is False
    assert info["reason"] == "robots_denied"


class FakeResponse:
    def __init__(self, status_code, body=b"ok", url="https://example.com/a", headers=None):
        self.status_code = status_code
        self._body = body
        self.url = url
        self.headers = headers or {"Content-Type": "text/html; charset=utf-8"}

    def iter_content(self, chunk_size=65536):
        yield self._body


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def get(self, *args, **kwargs):
        self.calls += 1
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_http_retry_and_no_retry_status(monkeypatch):
    monkeypatch.setattr(c.time, "sleep", lambda x: None)
    session = FakeSession([FakeResponse(500), FakeResponse(200, b"done")])
    result = c.request_with_retries(session, "https://example.com", max_retries=2)
    assert result["ok"] is True
    assert result["retry_count"] == 1
    session = FakeSession([FakeResponse(404)])
    result = c.request_with_retries(session, "https://example.com", max_retries=2)
    assert result["ok"] is False
    assert session.calls == 1
    assert result["error_type"] == "http_error_no_retry"


def test_html_content_blocks_order_list_and_table():
    title = zh(r"\u865a\u62df\u8d27\u5e01\u8bc8\u9a97\u6848\u4f8b")
    p_text = zh(r'\u5317\u4eac\u5e02\u4eba\u6c11\u6cd5\u9662\u5ba1\u7406\u8bc8\u9a97\u7f6a\u6848\u4ef6\uff0c\u6d89\u6848\u91d1\u989d\u4eba\u6c11\u5e01100\u4e07\u5143\u3002')
    li1_text = zh(r'\u4f7f\u7528USDT\u8f6c\u79fb\u8d44\u91d1')
    li2_text = zh(r'\u516c\u5b89\u673a\u5173\u4fa6\u529e')
    th1_text = zh(r'\u6848\u53f7')
    th2_text = zh(r'\u91d1\u989d')
    td1_text = zh(r'\uff082024\uff09\u4eac0101\u5211\u521d1\u53f7')
    td2_text = '100' + zh(r'\u4e07\u5143')

    # 现在在 f-string 中直接使用这些变量
    html = f"""
        <html><head><title>{title}</title><meta name="author" content="court"></head>
        <body><nav>nav</nav><article>
        <h1>{title}</h1>
        <p>{p_text}</p>
        <ul><li>{li1_text}</li><li>{li2_text}</li></ul>
        <table><tr><th>{th1_text}</th><th>{th2_text}</th></tr><tr><td>{td1_text}</td><td>{td2_text}</td></tr></table>
        </article></body></html>
        """
    extracted = c.extract_html(html.encode("utf-8"), "https://www.court.gov.cn/zixun/xiangqing/1.html", {"Content-Type": "text/html; charset=utf-8"})
    assert extracted["metadata"]["title"] == title
    assert [b["type"] for b in extracted["content_blocks"]] == ["heading", "paragraph", "list", "table"]
    assert [b["order"] for b in extracted["content_blocks"]] == [0, 1, 2, 3]
    assert len(extracted["lists"][0]["items"]) == 2
    assert extracted["tables"][0]["rows"][1][1] == "100" + zh(r"\u4e07\u5143")
    assert "USDT" in extracted["full_text"]


def test_rule_extraction_and_relevance_score():
    text = zh(r"\u5317\u4eac\u5e02\u4eba\u6c11\u6cd5\u9662\u5728\uff082024\uff09\u4eac0101\u5211\u521d1\u53f7\u4e2d\u8ba4\u5b9a\u8bc8\u9a97\u7f6a\uff0c\u6d89\u6848\u91d1\u989d\u4eba\u6c11\u5e01100\u4e07\u5143\uff0c\u4f7f\u7528USDT\u3002\u4f9d\u7167\u300a\u4e2d\u534e\u4eba\u6c11\u5171\u548c\u56fd\u5211\u6cd5\u300b\u7b2c\u4e8c\u767e\u516d\u5341\u516d\u6761\u3002")
    extracted = c.rule_extract(text)
    assert extracted["case_numbers"]
    assert extracted["institutions"]
    assert zh(r"\u8bc8\u9a97\u7f6a") in extracted["crimes"]
    assert any("100" in x for x in extracted["amounts"])
    score, basis = c.relevance_score(zh(r"\u865a\u62df\u8d27\u5e01\u8bc8\u9a97\u6848\u4f8b"), text, extracted)
    assert score >= 50
    assert basis["has_case_number"] is True


def test_pdf_url_not_filtered_when_enabled():
    source = make_source(allow_url_patterns=[r"\.pdf$"])
    result = c.url_allowed_by_config("https://www.court.gov.cn/a/b.pdf", source, "strict")
    assert result["allowed"] is True
    source_disabled = make_source(allow_url_patterns=[r"\.pdf$"], retain_pdf_documents=False)
    result = c.url_allowed_by_config("https://www.court.gov.cn/a/b.pdf", source_disabled, "strict")
    assert result["reason"] == "pdf_disabled"


def test_content_hash_duplicate_and_record_structure_jsonl(local_tmp_path):
    source = make_source()
    text = zh(r"\u865a\u62df\u8d27\u5e01\u8bc8\u9a97\u7f6a\u4eba\u6c11\u5e01100\u4e07\u5143")
    extraction = {
        "metadata": {"title": zh(r"\u865a\u62df\u8d27\u5e01\u6848\u4f8b")},
        "content_blocks": [{"order": 0, "type": "paragraph", "text": text}],
        "headings": [],
        "paragraphs": [{"order": 0, "type": "paragraph", "text": text}],
        "lists": [],
        "tables": [],
        "pdf_pages": [],
        "full_text": text,
        "encoding": "utf-8",
        "extraction_method": "test",
        "quality_flags": [],
    }
    fetch = {"status_code": 200, "final_url": source.seed_url, "headers": {"Content-Type": "text/html"}}
    seen = {c.sha256_text(extraction["full_text"])}
    record = c.build_record("run", source, source.seed_url, source.seed_url, None, 0, fetch, extraction, [], {"allowed": True}, seen)
    assert record["duplicates"]["content_duplicate"] is True
    path = local_tmp_path / "records.jsonl"
    with path.open("w", encoding="utf-8") as f:
        c.append_jsonl(f, record)
    loaded = json.loads(path.read_text(encoding="utf-8").strip())
    assert loaded["record_id"] == record["record_id"]


def test_atomic_state_save(local_tmp_path):
    path = local_tmp_path / "crawl_state.json"
    c.atomic_write_json(path, {"ok": True})
    assert json.loads(path.read_text(encoding="utf-8"))["ok"] is True
    assert not list(local_tmp_path.glob("*.tmp"))


def test_resume_force_refresh_and_failure_retry_helpers(local_tmp_path):
    state_path = local_tmp_path / "crawl_state.json"
    c.atomic_write_json(state_path, {"successful_urls": ["https://example.com/a"], "record_ids": ["r1"]})
    state = c.load_state(state_path)
    assert "https://example.com/a" in state["successful_urls"]
    assert c.make_record_id("s", "https://example.com/a") == c.make_record_id("s", "https://example.com/a")
    failures = local_tmp_path / "failures.jsonl"
    failures.write_text(json.dumps({"source_id": "s1", "url": "https://example.com/a", "depth": 1, "error_type": "network_timeout"}) + "\n", encoding="utf-8")
    retry = c.load_retry_urls(local_tmp_path, "network_timeout")
    assert retry == [("s1", "https://example.com/a", 1)]


def test_single_source_failure_does_not_block_dry_run(local_tmp_path):
    cfg = {
        "config_name": "virtual_currency_illegal_case_sources",
        "global_crawl_policy": {},
        "sources": [
            {"id": "bad", "domain": "example.com", "seed_url": "https://example.com/a", "allow_url_patterns": ["["], "deny_url_patterns": []},
            {"id": "good", "domain": "example.com", "seed_url": "https://example.com/b", "allow_url_patterns": ["/b"], "deny_url_patterns": []},
        ],
    }
    cfg_path = local_tmp_path / "cfg.json"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    args = c.build_arg_parser().parse_args(["--dry-run", "--config", str(cfg_path), "--output-dir", str(local_tmp_path / "out")])
    rc = c.dry_run(cfg_path, local_tmp_path / "out", args)
    assert rc == 1
    report = json.loads((local_tmp_path / "out" / "crawl_report.json").read_text(encoding="utf-8"))
    assert report["source_count"] == 2


def test_depth_and_page_limit_with_mocked_crawl(local_tmp_path, monkeypatch):
    cfg = {
        "config_name": "virtual_currency_illegal_case_sources",
        "global_crawl_policy": {"respect_robots_txt": False, "request_interval_seconds": 0},
        "sources": [
            {
                "id": "s1",
                "source_name": "test",
                "domain": "example.com",
                "seed_url": "https://example.com/case/1.html",
                "allow_url_patterns": [r"/case/1\.html"],
                "deny_url_patterns": [],
                "crawl_depth": 2,
            }
        ],
    }
    cfg_path = local_tmp_path / "cfg.json"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    html = ("<html><body><article><h1>" + zh(r"\u865a\u62df\u8d27\u5e01\u6848\u4ef6") + "</h1><p>" + zh(r"\u8bc8\u9a97\u7f6a\u4eba\u6c11\u5e01100\u4e07\u5143") + "</p><a href='/case/2.html'>" + zh(r"\u865a\u62df\u8d27\u5e01\u6848\u4ef6\u8be6\u60c5") + "</a></article></body></html>").encode("utf-8")
    monkeypatch.setattr(c, "make_session", lambda ua: object())
    monkeypatch.setattr(c.time, "sleep", lambda x: None)
    monkeypatch.setattr(
        c,
        "request_with_retries",
        lambda session, url, timeout, max_retries: {"ok": True, "status_code": 200, "retry_count": 0, "final_url": url, "headers": {"Content-Type": "text/html; charset=utf-8"}, "body": html},
    )
    args = c.build_arg_parser().parse_args(["--config", str(cfg_path), "--output-dir", str(local_tmp_path / "out"), "--max-pages-per-seed", "1", "--request-interval", "0", "--no-resume"])
    rc = c.crawl(cfg_path, local_tmp_path / "out", args)
    assert rc == 0
    records = [json.loads(x) for x in (local_tmp_path / "out" / "records.jsonl").read_text(encoding="utf-8").splitlines() if x]
    assert len(records) == 1
    expansion = json.loads((local_tmp_path / "out" / "expansion_report.json").read_text(encoding="utf-8"))
    assert expansion["sources"][0]["candidate_count"] == 1
