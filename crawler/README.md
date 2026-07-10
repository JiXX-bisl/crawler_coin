# Unified Crawler

Minimal unified crawler for `knowledge_ws`.

## Run a unified config

```powershell
python scripts/crawl.py --config configs/unified_crawl_example.json --dry-run
python scripts/crawl.py --config configs/unified_crawl_example.json
```

## Run legacy configs through adapters

```powershell
python scripts/crawl.py --config configs/virtual_currency_seed_urls.json --source-id btc_basic_001 --max-pages 1
python scripts/crawl.py --config configs/virtual_currency_illegal_case_sources.json --source-id source_001 --max-pages 1
```

## Config idea

The crawler reads a task-shaped config:

- `job`: task identity and open metadata
- `runtime`: request, retry, robots, and deduplication rules
- `sources`: start URLs plus fetcher/parser/discoverer/extractor declarations
- `url_rules`, `discovery`, `extraction`, `filters`: source-level behavior
- `metadata`: business fields passed through unchanged

Legacy knowledge and illegal-case configs are adapted at load time into the same internal model.
