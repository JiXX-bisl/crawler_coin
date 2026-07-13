# -*- coding: utf-8 -*-
import argparse
import json
from crawler.config.loader import load_config
from crawler.core.engine import CrawlEngine


def build_parser():
    parser = argparse.ArgumentParser(description="Run the unified crawler")
    parser.add_argument("--config", required=True, help="Unified crawl config JSON")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--source-id", action="append", default=[])
    parser.add_argument("--max-pages", type=int, default=None)
    parser.add_argument("--max-depth", type=int, default=None)
    parser.add_argument("--expansion-mode", choices=("strict", "controlled"), default=None)
    parser.add_argument("--no-progress", action="store_true", default=False)
    parser.add_argument("--dry-run", action="store_true", default=False)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    config = load_config(args.config, output_dir=args.output_dir)
    if args.expansion_mode:
        config.runtime.expansion_mode = args.expansion_mode
    if args.source_id:
        wanted = set(args.source_id)
        config.sources = [s for s in config.sources if s.id in wanted]
    for source in config.sources:
        if args.max_pages is not None:
            source.max_pages = args.max_pages
        if args.max_depth is not None:
            source.max_depth = args.max_depth
    if args.dry_run:
        print(
            json.dumps(
                {
                    "job": config.job,
                    "sources": len(config.sources),
                    "enabled_sources": len([s for s in config.sources if s.enabled]),
                    "expansion_mode": config.runtime.expansion_mode,
                    "output": config.output.__dict__,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    report = CrawlEngine(config, progress=not args.no_progress).run()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
