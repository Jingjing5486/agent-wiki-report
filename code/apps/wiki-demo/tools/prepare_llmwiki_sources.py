#!/usr/bin/env python3
"""Create page-locatable Markdown sources for the upstream llmwiki compiler.

The compiler cites Markdown line ranges, so every input PDF page is retained
as a headed section and a line-to-page crosswalk is written next to sources/.
Long PDFs are split only between physical PDF pages.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def safe_stem(name: str) -> str:
    stem = Path(name).stem.lower()
    stem = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", stem).strip("-")
    return stem[:72] or "source"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True, help="demo-v2 pages.jsonl")
    parser.add_argument("--output", type=Path, required=True, help="llmwiki project sources directory")
    parser.add_argument("--max-chars", type=int, default=50_000, help="maximum source size; split only at PDF page boundaries")
    args = parser.parse_args()
    if args.max_chars < 5_000:
        parser.error("--max-chars must be at least 5000")
    args.output.mkdir(parents=True, exist_ok=True)

    by_source: dict[str, list[dict]] = {}
    for line in args.input.open(encoding="utf-8"):
        row = json.loads(line)
        if row.get("disposition") == "exclude":
            continue
        text = (row.get("search_text") or "").strip()
        if not text:
            continue
        by_source.setdefault(row["source_id"], []).append(row)

    source_manifest = []
    crosswalk = []
    total_pages = 0
    for source_number, (source_id, pages) in enumerate(sorted(by_source.items()), 1):
        pages.sort(key=lambda row: int(row["page"]))
        source_name = pages[0]["source_name"]
        digest = pages[0]["source_sha256"]
        source_key = f"s{source_number:02d}-{safe_stem(source_name)}"
        chunks: list[list[dict]] = []
        current: list[dict] = []
        current_chars = 0
        for row in pages:
            block = f"## PDF物理页码 {row['page']}\n\n{row['search_text'].strip()}\n"
            if current and current_chars + len(block) > args.max_chars:
                chunks.append(current)
                current, current_chars = [], 0
            current.append(row)
            current_chars += len(block)
        if current:
            chunks.append(current)

        for chunk_number, chunk in enumerate(chunks, 1):
            first_page, last_page = int(chunk[0]["page"]), int(chunk[-1]["page"])
            filename = f"{source_key}-part{chunk_number:02d}-p{first_page:03d}-{last_page:03d}.md"
            lines = [
                f"# {source_name}",
                f"\n来源 ID：{source_id}",
                f"\n原始 PDF SHA-256：{digest}",
                f"\n本文件覆盖 PDF 物理页码：{first_page}–{last_page}",
                "",
            ]
            for row in chunk:
                # ``search_text`` may contain many embedded newlines. Count
                # physical Markdown lines rather than Python list elements so
                # citations from llmwiki map back to the correct PDF page.
                page_start = len("\n".join(lines).splitlines()) + 1
                page_text = row["search_text"].strip()
                lines.extend([f"## PDF物理页码 {row['page']}", "", row["search_text"].strip(), ""])
                page_end = page_start + 2 + len(page_text.splitlines())
                crosswalk.append({
                    "source_markdown": filename,
                    "source_id": source_id,
                    "source_name": source_name,
                    "source_sha256": digest,
                    "pdf_physical_page": int(row["page"]),
                    "line_start": page_start,
                    "line_end": page_end,
                    "quality_flags": row.get("quality_flags", []),
                    "ocr_status": row.get("ocr", {}).get("status"),
                    "disposition": row.get("disposition"),
                })
            (args.output / filename).write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
            source_manifest.append({
                "file": filename,
                "source_id": source_id,
                "source_name": source_name,
                "source_sha256": digest,
                "first_pdf_page": first_page,
                "last_pdf_page": last_page,
                "page_count": len(chunk),
                "characters": sum(len(row["search_text"]) for row in chunk),
            })
            total_pages += len(chunk)

    (args.output.parent / "source-manifest.json").write_text(
        json.dumps({"input": str(args.input), "max_chars": args.max_chars, "source_files": source_manifest,
                    "page_count": total_pages}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (args.output.parent / "line-to-pdf-page.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in crosswalk), encoding="utf-8"
    )
    print(f"Created {len(source_manifest)} source Markdown files covering {total_pages} pages.")
    print(f"Line crosswalk: {args.output.parent / 'line-to-pdf-page.jsonl'}")


if __name__ == "__main__":
    main()
