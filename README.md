# Unified Crawler

Run either crawl job through the same entry point:

```powershell
& 'D:\anaconda3\envs\crawler_0706\python.exe' main.py --config configs\coin_knowledge.json
& 'D:\anaconda3\envs\crawler_0706\python.exe' main.py --config configs\illegal_cases.json
```

`--config` is required. Limit a run to selected sources, pages, and HTML depth:

```powershell
& 'D:\anaconda3\envs\crawler_0706\python.exe' main.py --config configs\illegal_cases.json --source-id source_001 --max-pages 3 --max-depth 1
```

## Deep expansion

The default `controlled` mode expands only HTML pages. PDF, GitHub, Markdown, API, and other terminal content can be fetched, but never discovers more links. `strict` requires every candidate URL to match configured allow/deny rules.

```powershell
& 'D:\anaconda3\envs\crawler_0706\python.exe' main.py --config configs\illegal_cases.json --expansion-mode strict
& 'D:\anaconda3\envs\crawler_0706\python.exe' main.py --config configs\coin_knowledge.json --dry-run
```

Useful options: `--source-id`, `--max-pages`, `--max-depth`, `--expansion-mode`, `--output-dir`, `--dry-run`, and `--no-progress`.

Each run writes the following files to the configured output directory:

- `records.jsonl`
- `failures.jsonl`
- `crawl_report.json`
- `expansion_report.json`
- `source_associations.jsonl`
- `failure_domain_report.json`

`expansion_report.json` records discovered candidates, accepted HTML and terminal links, filtered reasons, and reasons a source could not expand. `source_associations.jsonl` preserves source metadata when a URL is already fetched by another source. `failure_domain_report.json` groups failures by domain and error type.

For illegal-case jobs, use `source_role` to make the crawl boundary explicit: `case_detail`, `topic_page`, `case_list`, `announcement_list`, or `pdf_report`. Only topic, list, and announcement sources can expand HTML links. A source may optionally define `request.verify_tls`, `request.trust_env`, and `request.proxies`; certificate validation remains enabled by default.

## Proxy port

Use the existing local proxy port when required:

```powershell
$env:HTTP_PROXY="http://127.0.0.1:7892"
$env:HTTPS_PROXY="http://127.0.0.1:7892"
```

`data/` is ignored by Git and crawl output is not committed.
