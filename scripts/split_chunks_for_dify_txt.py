# -*- coding: utf-8 -*-
"""
split_chunks_for_dify_txt.py

将已经完成本地 chunk 的 chunks.jsonl 转换为 4 个适合 Dify 上传的 TXT 文件：
1. 01_protocol_core_chunks_dify.txt
2. 02_cex_api_chunks_dify.txt
3. 03_risk_regulation_chunks_dify.txt
4. 04_balanced_first_rag_chunks_dify.txt

设计原则：
- 不再使用多列 CSV，避免 Dify 将 metadata 与正文错误切开；
- 每个 Dify block = metadata header + chunk 正文 + 统一分隔符；
- Dify 中 Delimiter 建议设置为：---DIFY_CHUNK_END---
- Dify 中 Maximum chunk length 建议设置为 4000，Chunk overlap 设置为 0。

建议执行：
python scripts/split_chunks_for_dify_txt.py --input data/virtual_currency_chunks/chunks.jsonl --out data/dify_txt --max-file-mb 14
"""

import argparse
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

DIFY_DELIMITER = "---DIFY_CHUNK_END---"

DEFAULT_MAX_FILE_MB = 14.0
DEFAULT_BALANCED_MAX_CEX_CHUNKS = 250
DEFAULT_BALANCED_MAX_CEX_PER_DOC = 40
DEFAULT_BALANCED_MAX_TOTAL_CHUNKS = 0  # 0 表示不限制

RISK_CATEGORIES = {
    "high_frequency_small_amount",
    "multi_input_multi_output",
    "address_consolidation",
    "fund_splitting",
    "peel_chain",
    "multi_hop_transfer",
    "cross_chain_jump",
    "approval_risk",
    "flash_loan",
    "mev_arbitrage",
    "privacy_mixer_transaction",
    "fincen_virtual_currency_guidance",
    "ofac_sanctioned_addresses",
    "fatf_red_flags",
    "exchange_compliance_reports",
    "onchain_analysis_reports",
}

RISK_AUTHORITIES = {
    "regulatory_authority",
    "industry_report",
    "academic_paper",
    "industry_reference",
}

CEX_AUTHORITIES = {
    "exchange_api",
}

CEX_CATEGORIES = {
    "cex_deposit_withdrawal",
}

PROTOCOL_DOMAINS = {
    "basic_concept",
    "asset_profile",
    "chain_model",
    "transaction_type",
}

EXCLUDE_FROM_PROTOCOL_CATEGORIES = {
    "cex_deposit_withdrawal",
}

METADATA_FIELDS = [
    "chunk_id",
    "doc_id",
    "source_id",
    "title",
    "url",
    "coin",
    "chain",
    "asset_type",
    "level_1",
    "level_2",
    "knowledge_domain",
    "knowledge_category",
    "source_name",
    "source_authority",
    "source_priority",
    "crawl_type",
    "source_type",
    "topic_tags",
    "chunk_index",
    "chunk_char_count",
]


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    records.append(obj)
                else:
                    print(f"[WARN] line {line_no}: not a JSON object, skipped")
            except Exception as exc:
                print(f"[WARN] line {line_no}: JSON parse failed: {exc}")
    return records


def normalize_text(text: Any) -> str:
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u200b", "").replace("\xa0", " ")
    # 保留段落结构，但避免大量空行
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_meta_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return " | ".join(str(x).strip() for x in value if str(x).strip())
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value).replace("\n", " ").replace("\r", " ").strip()


def get_chunk_text(record: Dict[str, Any]) -> str:
    """
    优先使用 chunk_text，避免使用 dify_text 导致 metadata + 正文重复。
    如果没有 chunk_text，则回退 content/text/dify_text。
    """
    for key in ["chunk_text", "content", "text"]:
        text = normalize_text(record.get(key, ""))
        if text:
            return text

    # 兜底：dify_text 可能已包含 metadata，但总比空文本好。
    return normalize_text(record.get("dify_text", ""))


def make_dify_block(record: Dict[str, Any], delimiter: str = DIFY_DELIMITER) -> str:
    title = normalize_meta_value(record.get("title", ""))
    chunk_text = get_chunk_text(record)
    chunk_text = chunk_text.replace(delimiter, " ").strip()

    header_lines = []
    for field in METADATA_FIELDS:
        value = normalize_meta_value(record.get(field, ""))
        if value:
            header_lines.append(f"[{field}={value}]")

    # 额外提供一行自然语言索引提示，增强中英混合检索稳定性。
    coin = normalize_meta_value(record.get("coin", ""))
    chain = normalize_meta_value(record.get("chain", ""))
    domain = normalize_meta_value(record.get("knowledge_domain", ""))
    category = normalize_meta_value(record.get("knowledge_category", ""))
    source = normalize_meta_value(record.get("source_name", ""))
    url = normalize_meta_value(record.get("url", ""))

    semantic_hint = (
        f"Document metadata: coin={coin}; chain={chain}; "
        f"domain={domain}; category={category}; source={source}; url={url}."
    )

    parts = []
    if header_lines:
        parts.append("\n".join(header_lines))
    parts.append(semantic_hint)
    if title:
        parts.append(f"Title: {title}")
    parts.append(chunk_text)

    block = "\n".join(p for p in parts if p.strip()).strip()
    return f"{block}\n{delimiter}\n"


def is_cex_api(record: Dict[str, Any]) -> bool:
    source_authority = normalize_meta_value(record.get("source_authority", "")).lower()
    knowledge_category = normalize_meta_value(record.get("knowledge_category", "")).lower()
    chain = normalize_meta_value(record.get("chain", "")).lower()
    asset_type = normalize_meta_value(record.get("asset_type", "")).lower()
    source_name = normalize_meta_value(record.get("source_name", "")).lower()

    return (
        source_authority in CEX_AUTHORITIES
        or knowledge_category in CEX_CATEGORIES
        or chain == "off-chain/cex"
        or asset_type == "exchange"
        or any(x in source_name for x in ["binance", "okx", "coinbase", "kraken"])
    )


def is_risk_regulation(record: Dict[str, Any]) -> bool:
    domain = normalize_meta_value(record.get("knowledge_domain", "")).lower()
    category = normalize_meta_value(record.get("knowledge_category", "")).lower()
    authority = normalize_meta_value(record.get("source_authority", "")).lower()
    level_1 = normalize_meta_value(record.get("level_1", ""))

    return (
        domain in {"risk_regulation", "transaction_feature"}
        or category in RISK_CATEGORIES
        or authority in RISK_AUTHORITIES
        or "风险" in level_1
        or "监管" in level_1
    )


def is_protocol_core(record: Dict[str, Any]) -> bool:
    domain = normalize_meta_value(record.get("knowledge_domain", "")).lower()
    category = normalize_meta_value(record.get("knowledge_category", "")).lower()
    if is_cex_api(record):
        return False
    if is_risk_regulation(record):
        return False
    if category in EXCLUDE_FROM_PROTOCOL_CATEGORIES:
        return False
    return domain in PROTOCOL_DOMAINS


def dedup_by_chunk_id(records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    for r in records:
        key = normalize_meta_value(r.get("chunk_id", "")) or json.dumps(r, sort_keys=True, ensure_ascii=False)
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def build_balanced_records(
    protocol_records: List[Dict[str, Any]],
    risk_records: List[Dict[str, Any]],
    cex_records: List[Dict[str, Any]],
    max_cex_chunks: int,
    max_cex_per_doc: int,
    max_total_chunks: int,
) -> List[Dict[str, Any]]:
    balanced: List[Dict[str, Any]] = []
    balanced.extend(protocol_records)
    balanced.extend(risk_records)

    # CEX/API 限额：防止 OKX / Binance 单一来源压制其他知识。
    per_doc_count: Dict[str, int] = defaultdict(int)
    selected_cex: List[Dict[str, Any]] = []

    # 优先保留 source_priority 更高、chunk_index 更靠前的内容。
    priority_order = {"A": 0, "B": 1, "C": 2}

    def sort_key(r: Dict[str, Any]) -> Tuple[int, str, int]:
        p = priority_order.get(normalize_meta_value(r.get("source_priority", "C")).upper(), 9)
        doc_id = normalize_meta_value(r.get("doc_id", ""))
        try:
            idx = int(r.get("chunk_index", 0))
        except Exception:
            idx = 0
        return (p, doc_id, idx)

    for r in sorted(cex_records, key=sort_key):
        if max_cex_chunks and len(selected_cex) >= max_cex_chunks:
            break
        doc_id = normalize_meta_value(r.get("doc_id", "")) or normalize_meta_value(r.get("source_id", ""))
        if max_cex_per_doc and per_doc_count[doc_id] >= max_cex_per_doc:
            continue
        selected_cex.append(r)
        per_doc_count[doc_id] += 1

    balanced.extend(selected_cex)
    balanced = dedup_by_chunk_id(balanced)

    if max_total_chunks and len(balanced) > max_total_chunks:
        balanced = balanced[:max_total_chunks]

    return balanced


def write_txt(records: List[Dict[str, Any]], path: Path, delimiter: str = DIFY_DELIMITER) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for r in records:
            text = get_chunk_text(r)
            if not text:
                continue
            f.write(make_dify_block(r, delimiter=delimiter))
            f.write("\n")


def split_txt_by_size(path: Path, max_file_mb: float) -> List[Path]:
    """
    如果单文件超过 Dify 限制，则按 DIFY_DELIMITER 切成 part 文件。
    返回实际建议上传的文件列表；若未拆分，返回原文件。
    """
    if max_file_mb <= 0:
        return [path]

    max_bytes = int(max_file_mb * 1024 * 1024)
    size = path.stat().st_size if path.exists() else 0
    if size <= max_bytes:
        return [path]

    text = path.read_text(encoding="utf-8", errors="ignore")
    raw_blocks = [b.strip() for b in text.split(DIFY_DELIMITER) if b.strip()]

    part_paths: List[Path] = []
    part_idx = 1
    current_blocks: List[str] = []
    current_size = 0

    def flush() -> None:
        nonlocal part_idx, current_blocks, current_size
        if not current_blocks:
            return
        part_path = path.with_name(f"{path.stem}_part{part_idx:03d}{path.suffix}")
        content = ""
        for b in current_blocks:
            content += b.strip() + "\n" + DIFY_DELIMITER + "\n\n"
        part_path.write_text(content, encoding="utf-8", newline="\n")
        part_paths.append(part_path)
        part_idx += 1
        current_blocks = []
        current_size = 0

    for block in raw_blocks:
        block_with_delim = block.strip() + "\n" + DIFY_DELIMITER + "\n\n"
        block_size = len(block_with_delim.encode("utf-8", errors="ignore"))
        if current_blocks and current_size + block_size > max_bytes:
            flush()
        current_blocks.append(block)
        current_size += block_size

    flush()
    return part_paths


def write_report(
    out_dir: Path,
    all_records: List[Dict[str, Any]],
    protocol: List[Dict[str, Any]],
    cex: List[Dict[str, Any]],
    risk: List[Dict[str, Any]],
    balanced: List[Dict[str, Any]],
    upload_files: Dict[str, List[Path]],
) -> None:
    def dist(records: List[Dict[str, Any]], field: str) -> Counter:
        return Counter(normalize_meta_value(r.get(field, "")) or "<EMPTY>" for r in records)

    report_path = out_dir / "dify_txt_export_report.txt"
    with report_path.open("w", encoding="utf-8") as f:
        f.write("Dify TXT export report\n")
        f.write("======================\n\n")
        f.write(f"input_chunks: {len(all_records)}\n")
        f.write(f"protocol_core_chunks: {len(protocol)}\n")
        f.write(f"cex_api_chunks: {len(cex)}\n")
        f.write(f"risk_regulation_chunks: {len(risk)}\n")
        f.write(f"balanced_first_rag_chunks: {len(balanced)}\n\n")

        f.write("outputs_for_upload:\n")
        for name, paths in upload_files.items():
            f.write(f"- {name}:\n")
            for p in paths:
                mb = p.stat().st_size / 1024 / 1024 if p.exists() else 0
                f.write(f"  - {p} ({mb:.2f} MB)\n")
        f.write("\n")

        for label, records in [
            ("protocol_core", protocol),
            ("cex_api", cex),
            ("risk_regulation", risk),
            ("balanced_first_rag", balanced),
        ]:
            f.write(f"[{label}]\n")
            for field in ["level_1", "knowledge_domain", "knowledge_category", "coin", "chain", "source_priority", "source_authority"]:
                f.write(f"distribution_by_{field}:\n")
                for k, v in dist(records, field).most_common(30):
                    f.write(f"- {k}: {v}\n")
                f.write("\n")

    print(f"[INFO] report: {report_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Split chunks.jsonl into Dify-friendly TXT files.")
    parser.add_argument("--input", required=True, help="输入 chunks.jsonl 路径")
    parser.add_argument("--out", required=True, help="输出目录，例如 data/dify_txt")
    parser.add_argument("--max-file-mb", type=float, default=DEFAULT_MAX_FILE_MB, help="Dify 单文件大小上限，默认 14MB；设为 0 表示不拆分")
    parser.add_argument("--balanced-max-cex-chunks", type=int, default=DEFAULT_BALANCED_MAX_CEX_CHUNKS, help="平衡库中最多保留多少个 CEX/API chunk")
    parser.add_argument("--balanced-max-cex-per-doc", type=int, default=DEFAULT_BALANCED_MAX_CEX_PER_DOC, help="平衡库中每个 CEX/API 文档最多保留多少个 chunk")
    parser.add_argument("--balanced-max-total-chunks", type=int, default=DEFAULT_BALANCED_MAX_TOTAL_CHUNKS, help="平衡库总 chunk 上限，0 表示不限制")
    parser.add_argument("--delimiter", default=DIFY_DELIMITER, help="Dify 自定义分隔符，默认 ---DIFY_CHUNK_END---")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_path = Path(args.input)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    records = read_jsonl(input_path)
    print(f"[INFO] loaded chunks: {len(records)}")

    # 去除空文本记录。
    records = [r for r in records if get_chunk_text(r)]
    print(f"[INFO] chunks with text: {len(records)}")

    cex_records = [r for r in records if is_cex_api(r)]
    risk_records = [r for r in records if is_risk_regulation(r) and not is_cex_api(r)]
    protocol_records = [r for r in records if is_protocol_core(r)]

    # 其他未归类内容可以不进入前三个专库，但可以根据需要后续另建 misc 库。
    protocol_records = dedup_by_chunk_id(protocol_records)
    cex_records = dedup_by_chunk_id(cex_records)
    risk_records = dedup_by_chunk_id(risk_records)

    balanced_records = build_balanced_records(
        protocol_records=protocol_records,
        risk_records=risk_records,
        cex_records=cex_records,
        max_cex_chunks=args.balanced_max_cex_chunks,
        max_cex_per_doc=args.balanced_max_cex_per_doc,
        max_total_chunks=args.balanced_max_total_chunks,
    )

    outputs = {
        "01_protocol_core_chunks_dify.txt": protocol_records,
        "02_cex_api_chunks_dify.txt": cex_records,
        "03_risk_regulation_chunks_dify.txt": risk_records,
        "04_balanced_first_rag_chunks_dify.txt": balanced_records,
    }

    upload_files: Dict[str, List[Path]] = {}
    for filename, recs in outputs.items():
        path = out_dir / filename
        write_txt(recs, path, delimiter=args.delimiter)
        part_paths = split_txt_by_size(path, args.max_file_mb)
        upload_files[filename] = part_paths
        print(f"[DONE] {filename}: records={len(recs)}, size={path.stat().st_size / 1024 / 1024:.2f} MB")
        if len(part_paths) > 1:
            print(f"       split into {len(part_paths)} parts")

    write_report(
        out_dir=out_dir,
        all_records=records,
        protocol=protocol_records,
        cex=cex_records,
        risk=risk_records,
        balanced=balanced_records,
        upload_files=upload_files,
    )

    print("\n[DIFY SETTINGS]")
    print(f"Delimiter: {args.delimiter}")
    print("Maximum chunk length: 4000")
    print("Chunk overlap: 0")
    print("Delete all URLs and email addresses: OFF")
    print("Summary Auto-Gen: OFF")
    print("Q&A chunking: OFF")


if __name__ == "__main__":
    main()
