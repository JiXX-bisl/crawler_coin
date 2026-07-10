# -*- coding: utf-8 -*-
import json
from pathlib import Path


class JsonlExporter:
    def __init__(self, output_dir, records_file, failures_file):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.records_path = self.output_dir / records_file
        self.failures_path = self.output_dir / failures_file
        self.records = self.records_path.open("a", encoding="utf-8")
        self.failures = self.failures_path.open("a", encoding="utf-8")

    def write_record(self, record):
        self.records.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        self.records.flush()

    def write_failure(self, failure):
        self.failures.write(json.dumps(failure, ensure_ascii=False, sort_keys=True) + "\n")
        self.failures.flush()

    def close(self):
        self.records.close()
        self.failures.close()
