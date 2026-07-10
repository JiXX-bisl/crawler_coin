# -*- coding: utf-8 -*-
"""
chunk_records.py

用于将已经完成质量筛选的虚拟货币知识记录 records_filtered.jsonl 切分为适合 RAG/知识库入库的 chunks。

推荐输入：
    data/virtual_currency_filtered/records_filtered.jsonl

推荐输出：
    data/virtual_currency_chunks/
        chunks.jsonl
        chunks.xlsx
        chunk_report.txt
        skipped_records.jsonl

执行示例：
    python scripts/chunk_records.py \
        --input data/virtual_currency_filtered/records_filtered.jsonl \
        --out data/virtual_currency_chunks \
        --mode auto \
        --min-chunk-chars 300

说明：
- 默认 mode=auto，会根据 crawl_type/source_type 自动选择 chunk_size 和 overlap。
- 默认不限制每篇文档 chunk 数量，以免丢失超长 API 文档内容。
- 如果希望控制超长文档权重，可增加：--max-chunks-per-doc 120
"""

import argparse
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    import pandas as pd
except Exception:  # pandas 仅用于导出 xlsx；没有也不影响 jsonl 输出
    pd = None  # type: ignore


# -----------------------------
# 基础工具
# -----------------------------

def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", errors="ignore")).hexdigest()


def safe_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    s = str(value).strip()
    return s if s else default


def safe_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, tuple):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return []
        # 兼容 "a|b" / "a,b" / JSON 字符串列表
        if s.startswith("[") and s.endswith("]"):
            try:
                arr = json.loads(s)
                if isinstance(arr, list):
                    return [str(x).strip() for x in arr if str(x).strip()]
            except Exception:
                pass
        for sep in ["|", "；", ";", "，", ","]:
            if sep in s:
                return [x.strip() for x in s.split(sep) if x.strip()]
        return [s]
    return [str(value).strip()] if str(value).strip() else []


def sanitize_id(value: str, fallback: str = "DOC") -> str:
    s = safe_str(value, fallback)
    s = re.sub(r"[^0-9a-zA-Z_\-]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or fallback


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
                if isinstance(item, dict):
                    item["_input_line_number"] = line_no
                    records.append(item)
                else:
                    print(f"[WARN] line={line_no} 不是 JSON object，已跳过")
            except Exception as exc:
                print(f"[WARN] JSON 解析失败 line={line_no}: {exc}")
    return records


def write_jsonl(records: Iterable[Dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


# -----------------------------
# 文本规范化与切分
# -----------------------------

def normalize_text(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u200b", "").replace("\xa0", " ")
    text = re.sub(r"[ \t\x0b\x0c]+", " ", text)

    # 保留段落结构，但压缩多余空行
    lines = [line.strip() for line in text.split("\n")]
    cleaned: List[str] = []
    blank_count = 0
    last_nonblank: Optional[str] = None
    for line in lines:
        if not line:
            blank_count += 1
            if blank_count <= 1:
                cleaned.append("")
            continue
        blank_count = 0
        # 去掉连续重复行，常见于导航栏或 API 文档目录
        if last_nonblank is not None and line == last_nonblank:
            continue
        cleaned.append(line)
        last_nonblank = line

    text = "\n".join(cleaned).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def split_long_unit(unit: str, max_len: int) -> List[str]:
    """将超长段落切成较小单元。优先按句号/分号/换行附近切分，失败时按固定窗口。"""
    unit = unit.strip()
    if len(unit) <= max_len:
        return [unit] if unit else []

    # 先按句子切分，中英文都兼容一些
    parts = re.split(r"(?<=[。！？!?\.])\s+", unit)
    if len(parts) > 1:
        out: List[str] = []
        buf = ""
        for p in parts:
            p = p.strip()
            if not p:
                continue
            if not buf:
                buf = p
            elif len(buf) + 1 + len(p) <= max_len:
                buf += " " + p
            else:
                if buf:
                    out.append(buf)
                buf = p
        if buf:
            out.append(buf)
        # 仍然过长的再递归固定切
        final: List[str] = []
        for x in out:
            if len(x) <= max_len:
                final.append(x)
            else:
                final.extend(fixed_window_split(x, max_len))
        return final

    return fixed_window_split(unit, max_len)


def fixed_window_split(text: str, max_len: int) -> List[str]:
    out: List[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + max_len, n)
        # 尽量在空格或标点处断开
        if end < n:
            cut_candidates = [
                text.rfind("\n", start, end),
                text.rfind(". ", start, end),
                text.rfind("; ", start, end),
                text.rfind("。", start, end),
                text.rfind("；", start, end),
                text.rfind(" ", start, end),
            ]
            cut = max(cut_candidates)
            if cut > start + int(max_len * 0.55):
                end = cut + 1
        piece = text[start:end].strip()
        if piece:
            out.append(piece)
        start = end
    return out


def text_to_units(text: str, unit_max_len: int) -> List[str]:
    """按段落切分为基本单元，超长段落再拆。"""
    text = normalize_text(text)
    if not text:
        return []

    paragraphs = re.split(r"\n\s*\n+", text)
    units: List[str] = []
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        # 如果一个段落内部有很多短行，按行再聚合，避免目录/表格结构丢失
        lines = [x.strip() for x in para.split("\n") if x.strip()]
        if len(lines) >= 8 and sum(len(x) for x in lines) / max(len(lines), 1) < 120:
            buf = ""
            for line in lines:
                if not buf:
                    buf = line
                elif len(buf) + 1 + len(line) <= unit_max_len:
                    buf += "\n" + line
                else:
                    units.extend(split_long_unit(buf, unit_max_len))
                    buf = line
            if buf:
                units.extend(split_long_unit(buf, unit_max_len))
        else:
            units.extend(split_long_unit(para, unit_max_len))

    return [u for u in units if u.strip()]


def tail_overlap(text: str, overlap: int) -> str:
    if overlap <= 0 or not text:
        return ""
    if len(text) <= overlap:
        return text.strip()

    tail = text[-overlap:]
    # 尽量从段落/句子边界开始
    for marker in ["\n\n", "\n", ". ", "。", "; ", "；", " "]:
        pos = tail.find(marker)
        if 0 <= pos < int(overlap * 0.5):
            tail = tail[pos + len(marker):]
            break
    return tail.strip()


def merge_units_to_chunks(
    units: List[str],
    chunk_size: int,
    overlap: int,
    min_chunk_chars: int,
) -> List[str]:
    if not units:
        return []

    chunks: List[str] = []
    buf = ""

    for unit in units:
        unit = unit.strip()
        if not unit:
            continue

        if not buf:
            buf = unit
            continue

        candidate_len = len(buf) + 2 + len(unit)
        if candidate_len <= chunk_size:
            buf = buf + "\n\n" + unit
        else:
            if buf.strip():
                chunks.append(buf.strip())
            ov = tail_overlap(buf, overlap)
            if ov:
                buf = ov + "\n\n" + unit
            else:
                buf = unit

            # 极端情况下 overlap + unit 仍远超 chunk_size，则再切一次
            if len(buf) > chunk_size * 1.5:
                sub_units = split_long_unit(buf, chunk_size)
                if len(sub_units) > 1:
                    for sub in sub_units[:-1]:
                        chunks.append(sub.strip())
                    buf = sub_units[-1].strip()

    if buf.strip():
        # 尾块过短时合并到上一块，避免产生无意义小 chunk
        if chunks and len(buf.strip()) < min_chunk_chars:
            chunks[-1] = (chunks[-1].rstrip() + "\n\n" + buf.strip()).strip()
        else:
            chunks.append(buf.strip())

    return [c for c in chunks if len(c.strip()) >= min_chunk_chars or len(chunks) == 1]


# -----------------------------
# 参数选择与元数据
# -----------------------------

def choose_chunk_params(record: Dict[str, Any], args: argparse.Namespace) -> Tuple[int, int]:
    """根据记录类型选择 chunk_size/overlap。"""
    if args.mode == "fixed":
        return int(args.chunk_size), int(args.overlap)

    crawl_type = safe_str(record.get("crawl_type") or record.get("source_type")).lower()
    knowledge_domain = safe_str(record.get("knowledge_domain")).lower()

    if crawl_type in {"api_doc"}:
        return int(args.api_chunk_size), int(args.api_overlap)
    if crawl_type in {"pdf"}:
        return int(args.pdf_chunk_size), int(args.pdf_overlap)
    if crawl_type in {"github_repo", "github_markdown"}:
        return int(args.github_chunk_size), int(args.github_overlap)

    # 风险/监管报告通常段落较长，略大一些
    if knowledge_domain in {"risk_regulation"}:
        return int(args.pdf_chunk_size), int(args.pdf_overlap)

    return int(args.chunk_size), int(args.overlap)


def choose_doc_cap(record: Dict[str, Any], args: argparse.Namespace) -> int:
    """
    返回每篇文档最多保留 chunk 数。0 表示不限制。
    默认不限制；如果用户传入 --max-chunks-per-doc，则启用总体上限，
    并可按类型进一步收紧。
    """
    base = int(args.max_chunks_per_doc)
    if base <= 0:
        return 0

    crawl_type = safe_str(record.get("crawl_type") or record.get("source_type")).lower()
    source_priority = safe_str(record.get("source_priority")).upper()

    cap = base
    if crawl_type in {"github_repo", "github_markdown"}:
        cap = min(cap, int(args.github_max_chunks))
    if crawl_type in {"api_doc"}:
        cap = min(cap, int(args.api_max_chunks))
    if source_priority == "C":
        cap = min(cap, int(args.c_priority_max_chunks))
    return cap


def build_metadata(record: Dict[str, Any]) -> Dict[str, Any]:
    """保留后续 RAG 检索需要的关键 metadata。"""
    return {
        "doc_id": safe_str(record.get("doc_id") or record.get("id") or record.get("source_id")),
        "source_id": safe_str(record.get("source_id") or record.get("id") or record.get("doc_id")),
        "seed_url": safe_str(record.get("seed_url") or record.get("normalized_url") or record.get("url")),
        "url": safe_str(record.get("url") or record.get("normalized_url") or record.get("seed_url")),
        "title": safe_str(record.get("title") or record.get("source_name")),
        "level_1": safe_str(record.get("level_1")),
        "level_2": safe_str(record.get("level_2")),
        "coin": safe_str(record.get("coin"), "N/A"),
        "chain": safe_str(record.get("chain"), "N/A"),
        "asset_type": safe_str(record.get("asset_type"), "N/A"),
        "knowledge_domain": safe_str(record.get("knowledge_domain")),
        "knowledge_category": safe_str(record.get("knowledge_category")),
        "source_name": safe_str(record.get("source_name")),
        "source_authority": safe_str(record.get("source_authority")),
        "source_priority": safe_str(record.get("source_priority")),
        "crawl_type": safe_str(record.get("crawl_type") or record.get("source_type")),
        "source_type": safe_str(record.get("source_type") or record.get("crawl_type")),
        "topic_tags": safe_list(record.get("topic_tags")),
        "topic_count": int(record.get("topic_count") or len(safe_list(record.get("topic_tags"))) or 0),
        "crawl_time": safe_str(record.get("crawl_time")),
        "content_hash": safe_str(record.get("content_hash") or record.get("effective_content_hash")),
        "char_count": int(record.get("char_count") or len(safe_str(record.get("content"))) or 0),
        "source_group": safe_str(record.get("source_group")),
        "notes": safe_str(record.get("notes")),
    }


def make_embedding_text(meta: Dict[str, Any], chunk_text: str) -> str:
    """给向量化使用的带上下文文本。chunk_text 保持原文；embedding_text 带轻量元数据。"""
    tags = ", ".join(meta.get("topic_tags") or [])
    header_parts = [
        f"Title: {meta.get('title', '')}".strip(),
        f"Coin: {meta.get('coin', '')}; Chain: {meta.get('chain', '')}; Domain: {meta.get('knowledge_domain', '')}; Category: {meta.get('knowledge_category', '')}".strip(),
        f"Source: {meta.get('source_name', '')}; URL: {meta.get('url', '')}".strip(),
    ]
    if tags:
        header_parts.append(f"Topic Tags: {tags}")
    header = "\n".join([x for x in header_parts if x and not x.endswith(":")])
    return f"{header}\n\n{chunk_text}".strip()


# -----------------------------
# 主流程
# -----------------------------

def chunk_one_record(record: Dict[str, Any], args: argparse.Namespace) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    status = safe_str(record.get("status"), "ok")
    content = normalize_text(safe_str(record.get("content")))
    if args.require_ok_status and status != "ok":
        return [], {"reason": "status_not_ok", "status": status, "url": record.get("url"), "title": record.get("title")}
    if not content:
        return [], {"reason": "empty_content", "url": record.get("url"), "title": record.get("title")}
    if len(content) < int(args.min_doc_chars):
        return [], {"reason": "doc_too_short", "char_count": len(content), "url": record.get("url"), "title": record.get("title")}

    chunk_size, overlap = choose_chunk_params(record, args)
    units = text_to_units(content, unit_max_len=chunk_size)
    chunk_texts = merge_units_to_chunks(
        units=units,
        chunk_size=chunk_size,
        overlap=overlap,
        min_chunk_chars=int(args.min_chunk_chars),
    )

    cap = choose_doc_cap(record, args)
    capped = False
    original_chunk_count = len(chunk_texts)
    if cap > 0 and len(chunk_texts) > cap:
        chunk_texts = chunk_texts[:cap]
        capped = True

    meta = build_metadata(record)
    doc_id = sanitize_id(meta.get("doc_id") or meta.get("source_id") or sha256_text(meta.get("url", ""))[:16], "DOC")

    chunks: List[Dict[str, Any]] = []
    for i, text in enumerate(chunk_texts, start=1):
        chunk_hash = sha256_text(text)
        chunk_id = f"{doc_id}_CHUNK_{i:04d}"
        item: Dict[str, Any] = {
            "chunk_id": chunk_id,
            "chunk_index": i - 1,
            "chunk_count_in_doc": len(chunk_texts),
            "chunk_char_count": len(text),
            "chunk_hash": chunk_hash,
            "chunk_text": text,
            # content 字段用于兼容部分知识库导入器；完整元数据仍保留在其他字段中
            "content": text,
            # embedding_text 带轻量上下文，后续可选择用它做向量化
            "embedding_text": make_embedding_text(meta, text),
            "chunk_size_param": chunk_size,
            "overlap_param": overlap,
            "doc_original_chunk_count": original_chunk_count,
            "doc_capped": capped,
        }
        item.update(meta)
        chunks.append(item)

    return chunks, None


def export_xlsx(chunks: List[Dict[str, Any]], path: Path, preview_chars: int) -> None:
    if pd is None:
        print("[WARN] pandas 未安装，跳过 xlsx 导出。")
        return
    rows: List[Dict[str, Any]] = []
    for r in chunks:
        row = dict(r)
        chunk_text = safe_str(row.get("chunk_text"))
        embedding_text = safe_str(row.get("embedding_text"))
        row.pop("chunk_text", None)
        row.pop("content", None)
        row.pop("embedding_text", None)
        row["chunk_text_preview"] = chunk_text[:preview_chars]
        row["embedding_text_preview"] = embedding_text[:preview_chars]
        # Excel 中 list 不方便看，转为字符串
        if isinstance(row.get("topic_tags"), list):
            row["topic_tags"] = "|".join(row["topic_tags"])
        rows.append(row)

    if rows:
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).to_excel(path, index=False)


def write_report(
    path: Path,
    input_path: Path,
    out_dir: Path,
    records: List[Dict[str, Any]],
    chunks: List[Dict[str, Any]],
    skipped: List[Dict[str, Any]],
    args: argparse.Namespace,
) -> None:
    def count_by(field: str) -> Counter:
        return Counter(safe_str(c.get(field), "N/A") for c in chunks)

    chunks_per_doc = Counter(c.get("doc_id") for c in chunks)
    capped_docs = sorted({c.get("doc_id") for c in chunks if c.get("doc_capped")})
    chunk_lengths = [int(c.get("chunk_char_count") or 0) for c in chunks]

    lines: List[str] = []
    lines.append(f"chunk_time: {now_str()}")
    lines.append(f"input: {input_path}")
    lines.append(f"out_dir: {out_dir}")
    lines.append("")
    lines.append("parameters:")
    lines.append(f"- mode: {args.mode}")
    lines.append(f"- chunk_size: {args.chunk_size}")
    lines.append(f"- overlap: {args.overlap}")
    lines.append(f"- min_doc_chars: {args.min_doc_chars}")
    lines.append(f"- min_chunk_chars: {args.min_chunk_chars}")
    lines.append(f"- max_chunks_per_doc: {args.max_chunks_per_doc}")
    lines.append("")
    lines.append("summary:")
    lines.append(f"- input_records: {len(records)}")
    lines.append(f"- skipped_records: {len(skipped)}")
    lines.append(f"- chunked_documents: {len(chunks_per_doc)}")
    lines.append(f"- total_chunks: {len(chunks)}")
    if chunks_per_doc:
        vals = list(chunks_per_doc.values())
        lines.append(f"- avg_chunks_per_doc: {sum(vals) / len(vals):.2f}")
        lines.append(f"- max_chunks_per_doc_actual: {max(vals)}")
    if chunk_lengths:
        sorted_len = sorted(chunk_lengths)
        mid = sorted_len[len(sorted_len) // 2]
        p95 = sorted_len[min(len(sorted_len) - 1, math.floor(len(sorted_len) * 0.95))]
        lines.append(f"- min_chunk_chars_actual: {min(sorted_len)}")
        lines.append(f"- median_chunk_chars_actual: {mid}")
        lines.append(f"- avg_chunk_chars_actual: {sum(sorted_len) / len(sorted_len):.2f}")
        lines.append(f"- p95_chunk_chars_actual: {p95}")
        lines.append(f"- max_chunk_chars_actual: {max(sorted_len)}")
    lines.append(f"- capped_docs: {len(capped_docs)}")
    lines.append("")

    if skipped:
        lines.append("skipped_reasons:")
        for k, v in Counter(safe_str(x.get("reason"), "unknown") for x in skipped).most_common():
            lines.append(f"- {k}: {v}")
        lines.append("")

    for field in ["level_1", "knowledge_domain", "knowledge_category", "coin", "chain", "source_priority", "crawl_type", "source_authority"]:
        lines.append(f"distribution_by_{field}:")
        for k, v in count_by(field).most_common():
            lines.append(f"- {k}: {v}")
        lines.append("")

    lines.append("top_docs_by_chunk_count:")
    doc_title = {}
    doc_url = {}
    for c in chunks:
        doc_title[c.get("doc_id")] = c.get("title")
        doc_url[c.get("doc_id")] = c.get("url")
    for doc_id, n in chunks_per_doc.most_common(30):
        title = safe_str(doc_title.get(doc_id))[:120]
        url = safe_str(doc_url.get(doc_id))
        lines.append(f"- {doc_id}: chunks={n} | {title} | {url}")
    lines.append("")

    if capped_docs:
        lines.append("capped_docs:")
        for doc_id in capped_docs[:100]:
            lines.append(f"- {doc_id}")
        if len(capped_docs) > 100:
            lines.append(f"- ... and {len(capped_docs) - 100} more")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def process(args: argparse.Namespace) -> None:
    input_path = Path(args.input)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not input_path.exists():
        raise FileNotFoundError(f"输入文件不存在: {input_path}")

    records = read_jsonl(input_path)
    print(f"[INFO] Loaded records: {len(records)}")

    all_chunks: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    for idx, rec in enumerate(records, start=1):
        chunks, skip = chunk_one_record(rec, args)
        if skip:
            skip["_input_index"] = idx
            skip["doc_id"] = rec.get("doc_id") or rec.get("id") or rec.get("source_id")
            skipped.append(skip)
            continue
        all_chunks.extend(chunks)

    # chunk_hash 去重，避免 overlap/重复内容导致完全重复 chunk
    seen_hashes = set()
    unique_chunks: List[Dict[str, Any]] = []
    dup_count = 0
    for c in all_chunks:
        h = c.get("chunk_hash")
        if h in seen_hashes:
            dup_count += 1
            continue
        seen_hashes.add(h)
        unique_chunks.append(c)

    chunks_path = out_dir / "chunks.jsonl"
    xlsx_path = out_dir / "chunks.xlsx"
    skipped_path = out_dir / "skipped_records.jsonl"
    report_path = out_dir / "chunk_report.txt"

    write_jsonl(unique_chunks, chunks_path)
    write_jsonl(skipped, skipped_path)
    export_xlsx(unique_chunks, xlsx_path, preview_chars=int(args.excel_preview_chars))
    write_report(report_path, input_path, out_dir, records, unique_chunks, skipped, args)

    print("[DONE] Chunking finished.")
    print(f"[INFO] input_records: {len(records)}")
    print(f"[INFO] skipped_records: {len(skipped)}")
    print(f"[INFO] chunks_before_dedup: {len(all_chunks)}")
    print(f"[INFO] duplicate_chunks_removed: {dup_count}")
    print(f"[INFO] total_chunks: {len(unique_chunks)}")
    print(f"[INFO] chunks_jsonl: {chunks_path}")
    print(f"[INFO] chunks_xlsx: {xlsx_path}")
    print(f"[INFO] report: {report_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Chunk filtered virtual currency knowledge records for RAG ingestion.")

    parser.add_argument("--input", required=True, help="输入 records_filtered.jsonl 路径")
    parser.add_argument("--out", required=True, help="输出目录，例如 data/virtual_currency_chunks")

    parser.add_argument("--mode", choices=["auto", "fixed"], default="auto", help="auto 根据文档类型选择参数；fixed 使用统一参数")
    parser.add_argument("--chunk-size", type=int, default=2000, help="默认 chunk 字符数，fixed 模式或普通 html 使用")
    parser.add_argument("--overlap", type=int, default=250, help="默认 overlap 字符数")

    parser.add_argument("--api-chunk-size", type=int, default=2500, help="api_doc 文档 chunk 字符数")
    parser.add_argument("--api-overlap", type=int, default=300, help="api_doc 文档 overlap 字符数")
    parser.add_argument("--pdf-chunk-size", type=int, default=2000, help="pdf/监管报告 chunk 字符数")
    parser.add_argument("--pdf-overlap", type=int, default=250, help="pdf/监管报告 overlap 字符数")
    parser.add_argument("--github-chunk-size", type=int, default=1600, help="github_repo/github_markdown chunk 字符数")
    parser.add_argument("--github-overlap", type=int, default=200, help="github_repo/github_markdown overlap 字符数")

    parser.add_argument("--min-doc-chars", type=int, default=500, help="文档正文少于该值则跳过")
    parser.add_argument("--min-chunk-chars", type=int, default=300, help="chunk 少于该值则合并或跳过")
    parser.add_argument("--max-chunks-per-doc", type=int, default=0, help="每篇文档最多保留多少 chunk；0 表示不限制")
    parser.add_argument("--github-max-chunks", type=int, default=60, help="启用上限时，GitHub 文档每篇最多保留 chunk 数")
    parser.add_argument("--api-max-chunks", type=int, default=120, help="启用上限时，API 文档每篇最多保留 chunk 数")
    parser.add_argument("--c-priority-max-chunks", type=int, default=80, help="启用上限时，C 类来源每篇最多保留 chunk 数")

    parser.add_argument("--excel-preview-chars", type=int, default=1200, help="xlsx 中 chunk 文本预览长度")
    parser.add_argument("--require-ok-status", action="store_true", help="只处理 status == ok 的记录；当前 filtered 文件通常已满足")

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    process(args)


if __name__ == "__main__":
    main()
