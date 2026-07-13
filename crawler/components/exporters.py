# -*- coding: utf-8 -*-
import json
from pathlib import Path


class JsonlExporter:
    def __init__(self, output_dir, records_file, failures_file, associations_file="source_associations.jsonl"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.records = (self.output_dir / records_file).open("a", encoding="utf-8")
        self.failures = (self.output_dir / failures_file).open("a", encoding="utf-8")
        self.associations = (self.output_dir / associations_file).open("a", encoding="utf-8")

    @staticmethod
    def _write(handle, value):
        handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()

    def write_record(self, record):
        self._write(self.records, record)

    def write_failure(self, failure):
        self._write(self.failures, failure)

    def write_association(self, association):
        self._write(self.associations, association)

    def close(self):
        self.records.close()
        self.failures.close()
        self.associations.close()
