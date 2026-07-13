# -*- coding: utf-8 -*-
"""Generate a clean two-column corpus from crawler records."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from crawler.cleaning.pipeline import CleaningPipeline, clean_jsonl, load_rules


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Clean crawler records into label/content JSONL.")
    parser.add_argument("--input", required=True, help="Path to crawler records.jsonl")
    parser.add_argument("--output-dir", required=True, help="Directory for cleaned data and audit files")
    parser.add_argument("--dataset", choices=("auto", "coin_knowledge", "illegal_cases"), default="auto")
    parser.add_argument("--rules", default="configs/data_cleaning_rules.json")
    parser.add_argument("--min-chars", type=int, default=200)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.min_chars < 1:
        raise SystemExit("--min-chars must be positive")
    if not Path(args.input).is_file():
        raise SystemExit(f"Input file does not exist: {args.input}")
    if not Path(args.rules).is_file():
        raise SystemExit(f"Rules file does not exist: {args.rules}")

    pipeline = CleaningPipeline(load_rules(args.rules), dataset=args.dataset, min_chars=args.min_chars)
    report = clean_jsonl(args.input, args.output_dir, pipeline, dry_run=args.dry_run)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
