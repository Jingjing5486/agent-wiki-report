"""Build a reusable, auditable page/layout corpus from the fixed source folder.

Original PDFs are read-only. The generated JSONL files are derived data and can
be rebuilt from the source hashes recorded in manifest.json.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import fitz
from PIL import Image, ImageOps


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def text_of_block(block: dict) -> str:
    return "\n".join(
        "".join(span.get("text", "") for span in line.get("spans", []))
        for line in block.get("lines", [])
    ).strip()


def collect_blocks(page: fitz.Page) -> list[dict]:
    result = []
    for index, block in enumerate(page.get_text("dict", sort=True).get("blocks", [])):
        bbox = [round(float(v), 2) for v in block.get("bbox", [])]
        if block.get("type") == 0:
            lines = []
            for line in block.get("lines", []):
                spans = [{
                    "text": span.get("text", ""),
                    "font": span.get("font", ""),
                    "size": round(float(span.get("size", 0)), 2),
                    "flags": int(span.get("flags", 0)),
                    "color": int(span.get("color", 0)),
                    "bbox": [round(float(v), 2) for v in span.get("bbox", [])],
                } for span in line.get("spans", [])]
                lines.append({
                    "bbox": [round(float(v), 2) for v in line.get("bbox", [])],
                    "spans": spans,
                })
            item = {"block_id": f"b{index:04d}", "type": "text", "bbox": bbox,
                    "text": text_of_block(block), "lines": lines}
        else:
            item = {"block_id": f"b{index:04d}", "type": "image", "bbox": bbox,
                    "width": block.get("width"), "height": block.get("height")}
        result.append(item)
    return result


def candidate_headings(blocks: list[dict]) -> list[dict]:
    sizes = [span["size"] for block in blocks if block["type"] == "text"
             for line in block["lines"] for span in line["spans"]
             if span["text"].strip() and span["size"] > 0]
    if not sizes:
        return []
    body_size = sorted(sizes)[len(sizes) // 2]
    candidates = []
    for block in blocks:
        if block["type"] != "text" or not block["text"]:
            continue
        spans = [span for line in block["lines"] for span in line["spans"]]
        max_size = max((span["size"] for span in spans), default=0)
        bold = any("bold" in span["font"].lower() or span["flags"] & 16 for span in spans)
        if len(block["text"]) <= 100 and (max_size >= max(14.0, body_size * 1.22) or
                                           (bold and len(block["text"]) <= 70)):
            candidates.append({"block_id": block["block_id"], "text": block["text"],
                               "font_size": max_size, "bold": bold,
                               "bbox": block["bbox"], "status": "heuristic_candidate"})
    return candidates


def image_coverage(page: fitz.Page) -> float:
    page_area = max(float(page.rect.get_area()), 1.0)
    area = 0.0
    seen = set()
    for image in page.get_images(full=True):
        xref = image[0]
        for rect in page.get_image_rects(xref):
            key = tuple(round(float(x), 1) for x in rect)
            if key not in seen:
                seen.add(key)
                area += float(rect.get_area())
    return round(min(area / page_area, 1.0), 4)


def extract_tables(page: fitz.Page, page_number: int, source_id: str) -> list[dict]:
    try:
        found = page.find_tables()
    except Exception as exc:
        return [{"source_id": source_id, "page": page_number,
                 "table_id": f"{source_id}-p{page_number}-error",
                 "classification": "extract_error", "error": str(exc), "rows": []}]
    tables = []
    for i, table in enumerate(found.tables):
        rows = table.extract() or []
        normalized = [[cell.strip() if isinstance(cell, str) else "" for cell in row]
                      for row in rows]
        nonempty = [cell for row in normalized for cell in row if cell]
        columns = max((len(row) for row in normalized), default=0)
        nonempty_columns = sum(any(row[i] for row in normalized if i < len(row))
                               for i in range(columns))
        fill = len(nonempty) / max(sum(len(row) for row in normalized), 1)
        max_cell_length = max((len(cell) for cell in nonempty), default=0)
        mean_cell_length = sum(map(len, nonempty)) / max(len(nonempty), 1)
        if (len(normalized) >= 2 and columns >= 2 and nonempty_columns >= 2 and
                len(nonempty) >= 4 and fill >= 0.12 and max_cell_length <= 240 and
                mean_cell_length <= 100):
            classification = "structured_candidate"
        elif columns >= 2 and nonempty_columns >= 2:
            classification = "layout_or_complex_table_needs_review"
        else:
            classification = "false_positive_text_region"
        tables.append({
            "source_id": source_id,
            "page": page_number,
            "table_id": f"{source_id}-p{page_number}-t{i + 1}",
            "bbox": [round(float(v), 2) for v in table.bbox],
            "row_count": len(normalized),
            "column_count": columns,
            "fill_ratio": round(fill, 4),
            "nonempty_column_count": nonempty_columns,
            "max_cell_length": max_cell_length,
            "mean_cell_length": round(mean_cell_length, 2),
            "rows": normalized,
            "classification": classification,
        })
    return tables


def detect_two_columns(image: Image.Image) -> float | None:
    gray = ImageOps.grayscale(image).resize((360, 120))
    pixels = list(gray.getdata())
    counts = [sum(pixels[y * 360 + x] < 190 for y in range(120)) for x in range(360)]
    lo, hi = int(360 * 0.44), int(360 * 0.56)
    candidate = min(range(lo, hi), key=lambda x: counts[x])
    central_mean = sum(counts[lo:hi]) / (hi - lo)
    left_fill = sum(counts[int(360 * 0.12):candidate])
    right_fill = sum(counts[candidate + 1:int(360 * 0.88)])
    if central_mean and counts[candidate] / central_mean <= 0.16 and left_fill > 500 and right_fill > 500:
        return candidate / 360
    return None


def tesseract_region(image_path: Path, output_base: Path, language: str,
                     psm: int) -> tuple[str, list[dict], str | None]:
    proc = subprocess.run(
        [shutil.which("tesseract"), str(image_path), str(output_base), "-l", language,
         "--psm", str(psm), "tsv", "txt"],
        check=False, capture_output=True, text=True,
    )
    if proc.returncode:
        return "", [], proc.stderr.strip() or f"exit={proc.returncode}"
    text_path, tsv_path = output_base.with_suffix(".txt"), output_base.with_suffix(".tsv")
    text = text_path.read_text(encoding="utf-8", errors="replace").strip()
    tokens = []
    for row in tsv_path.read_text(encoding="utf-8", errors="replace").splitlines()[1:]:
        fields = row.split("\t", 11)
        if len(fields) != 12 or fields[0] != "5" or not fields[11].strip():
            continue
        try:
            confidence = float(fields[10])
        except ValueError:
            continue
        if confidence >= 0:
            tokens.append({"text": fields[11].strip(), "confidence": round(confidence / 100, 4),
                           "bbox_px": [int(fields[6]), int(fields[7]), int(fields[8]), int(fields[9])]})
    return text, tokens, None


def tesseract_ocr(page: fitz.Page, language: str, scale: float = 2.5) -> dict:
    executable = shutil.which("tesseract")
    if not executable:
        return {"status": "required_but_engine_missing", "text": "", "lines": [],
                "mean_confidence": None, "error": "tesseract executable not found"}
    with tempfile.TemporaryDirectory(prefix="wiki-ocr-") as tmp:
        tmp_path = Path(tmp)
        image_path = tmp_path / "page.png"
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        pix.save(image_path)
        image = Image.open(image_path).convert("RGB")
        split = detect_two_columns(image)
        width, height = image.size
        if split is None:
            regions = [("full", (0, 0, width, height), 0, 3)]
        else:
            gutter = int(width * 0.012)
            x = int(width * split)
            regions = [("left", (0, 0, max(1, x - gutter), height), 0, 6),
                       ("right", (min(width - 1, x + gutter), 0, width, height), x + gutter, 6)]
        text_parts, word_boxes, confidences = [], [], []
        for name, box, x_offset, psm in regions:
            crop_path = tmp_path / f"{name}.png"
            image.crop(box).save(crop_path)
            text_part, tokens, error = tesseract_region(
                crop_path, tmp_path / f"{name}-ocr", language, psm)
            if error:
                return {"status": "failed", "text": "", "word_boxes": [],
                        "mean_confidence": None, "layout": "two_column" if split else "full_page",
                        "error": error}
            if text_part:
                text_parts.append(text_part)
            for token in tokens:
                x0, y0, w, h = token.pop("bbox_px")
                token["bbox_norm"] = [(x_offset + x0) / width, y0 / height,
                                      (x_offset + x0 + w) / width, (y0 + h) / height]
                token["region"] = name
                word_boxes.append(token)
                confidences.append(token["confidence"])
        text = "\n\n".join(text_parts)
        mean = sum(confidences) / len(confidences) if confidences else None
    return {"status": "ok" if text.strip() else "no_text_found", "text": text,
            "word_boxes": word_boxes, "mean_confidence": round(mean, 4) if mean is not None else None,
            "layout": "two_column" if split else "full_page", "error": None}


def page_flags(native_text: str, image_ratio: float, ocr: dict,
               repeated: bool, table_count: int) -> list[str]:
    flags = []
    chars = len(native_text.strip())
    if chars < 80:
        flags.append("low_native_text")
    if image_ratio >= 0.25 and chars < 250:
        flags.append("image_text_may_be_missing")
    if "required_but_engine_missing" == ocr["status"]:
        flags.append("ocr_pending")
    if ocr["status"] == "failed":
        flags.append("ocr_failed")
    if ocr["status"] == "ok" and (ocr["mean_confidence"] or 0) < 0.90:
        flags.append("ocr_needs_visual_review")
    if repeated:
        flags.append("duplicate_native_page_text")
    if "\ufffd" in native_text:
        flags.append("replacement_characters")
    if table_count:
        flags.append("table_extraction_visual_review")
    return flags


def prepare(input_dir: Path, output_dir: Path, language: str, ocr_scale: float) -> dict:
    input_dir = input_dir.resolve()
    output_dir = output_dir.resolve()
    if not input_dir.is_dir():
        raise ValueError(f"Input directory not found: {input_dir}")
    if output_dir.exists():
        raise FileExistsError(f"Output already exists: {output_dir}")
    pdfs = sorted(input_dir.glob("*.pdf"))
    if not pdfs:
        raise ValueError("No PDFs found")
    output_dir.mkdir(parents=True)
    pages, sources, tables, table_candidates, reviews, exclusions = [], [], [], [], [], []

    for pdf_path in pdfs:
        raw = pdf_path.read_bytes()
        source_id = "src-" + sha256_bytes(pdf_path.name.encode("utf-8"))[:16]
        doc = fitz.open(pdf_path)
        toc = [{"level": int(x[0]), "title": x[1], "page": int(x[2])}
               for x in doc.get_toc(simple=True)]
        source_page_rows = []
        seen_page_hashes = set()

        for page_number, page in enumerate(doc, 1):
            native_text = page.get_text("text", sort=True) or ""
            blocks = collect_blocks(page)
            page_table_candidates = extract_tables(page, page_number, source_id)
            table_candidates.extend(page_table_candidates)
            page_tables = [t for t in page_table_candidates
                           if t["classification"] == "structured_candidate"]
            tables.extend(page_tables)
            ratio = image_coverage(page)
            needs_ocr = (len(native_text.strip()) < 80 or
                         (ratio >= 0.25 and len(native_text.strip()) < 250))
            ocr = (tesseract_ocr(page, language, ocr_scale) if needs_ocr else
                   {"status": "not_required_native_text", "text": "", "lines": [],
                    "mean_confidence": None, "error": None})
            page_hash = sha256_bytes(native_text.strip().encode("utf-8"))
            duplicate = bool(native_text.strip()) and page_hash in seen_page_hashes
            if native_text.strip():
                seen_page_hashes.add(page_hash)
            table_review_needed = any(t["classification"] != "structured_candidate"
                                      for t in page_table_candidates)
            flags = page_flags(native_text, ratio, ocr, duplicate, int(table_review_needed))

            # OCR is separate evidence. It is added to search text only on pages
            # where the native layer is absent or clearly sparse.
            searchable = native_text.strip()
            if needs_ocr and ocr["status"] == "ok":
                ocr_lines = [line for line in ocr["text"].splitlines() if line.strip()]
                normalized_native = re.sub(r"\s+", "", searchable).lower()
                supplements = [line for line in ocr_lines
                               if re.sub(r"\s+", "", line).lower() not in normalized_native]
                searchable = "\n".join(x for x in [searchable, "\n".join(supplements)] if x)

            toc_items = [x for x in toc if x["page"] == page_number]
            row = {
                "source_id": source_id, "source_name": pdf_path.name,
                "source_sha256": sha256_bytes(raw), "page": page_number,
                "page_count": len(doc),
                "page_size_pt": [round(page.rect.width, 2), round(page.rect.height, 2)],
                "native_text": native_text, "search_text": searchable,
                "native_char_count": len(native_text.strip()),
                "image_coverage_ratio": ratio,
                "outline_headings": toc_items,
                "heading_candidates": candidate_headings(blocks),
                "blocks": blocks,
                "table_ids": [x["table_id"] for x in page_tables],
                "table_candidate_ids": [x["table_id"] for x in page_table_candidates],
                "ocr": ocr, "quality_flags": flags,
                "disposition": "exclude_from_search_if_empty_after_ocr"
                    if not searchable.strip() else "keep_pending_review"
            }
            pages.append(row)
            source_page_rows.append({"page": page_number,
                                     "native_char_count": row["native_char_count"],
                                     "ocr_status": ocr["status"],
                                     "quality_flags": flags,
                                     "table_count": len(page_tables),
                                     "table_candidate_count": len(page_table_candidates)})
            if flags:
                reviews.append({
                    "source_id": source_id, "source_name": pdf_path.name,
                    "page": page_number, "native_char_count": row["native_char_count"],
                    "image_coverage_ratio": ratio, "ocr_status": ocr["status"],
                    "ocr_confidence": ocr["mean_confidence"],
                    "table_count": len(page_tables),
                    "table_candidate_count": len(page_table_candidates), "flags": ";".join(flags),
                    "sample_native_text": re.sub(r"\s+", " ", native_text)[:240],
                    "sample_ocr_text": re.sub(r"\s+", " ", ocr["text"])[:240],
                })
            if not searchable.strip():
                exclusions.append({"source_id": source_id, "source_name": pdf_path.name,
                                   "page": page_number, "reason": "no_native_or_ocr_text",
                                   "action": "exclude_from_search_keep_source_and_page_record"})

        sources.append({
            "source_id": source_id, "source_name": pdf_path.name,
            "source_sha256": sha256_bytes(raw), "byte_size": len(raw),
            "page_count": len(doc), "outline": toc,
            "pages": source_page_rows,
        })
        doc.close()

    write_jsonl(output_dir / "sources.jsonl", sources)
    write_jsonl(output_dir / "pages.jsonl", pages)
    write_jsonl(output_dir / "tables.jsonl", tables)
    write_jsonl(output_dir / "table_candidates.jsonl", table_candidates)
    write_jsonl(output_dir / "exclusions.jsonl", exclusions)
    if reviews:
        with (output_dir / "review.csv").open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=list(reviews[0].keys()))
            writer.writeheader()
            writer.writerows(reviews)
    else:
        (output_dir / "review.csv").write_text("", encoding="utf-8-sig")

    pending_ocr = sum(p["ocr"]["status"] in {"required_but_engine_missing", "failed"}
                      for p in pages)
    empty_pages = sum(not p["search_text"].strip() for p in pages)
    dataset_notes = input_dir.parent / "data_资料说明.md"
    notes_record = None
    if dataset_notes.exists():
        shutil.copy2(dataset_notes, output_dir / "data_资料说明.md")
        notes_record = {"path": str(dataset_notes), "sha256": sha256_bytes(dataset_notes.read_bytes())}
    summary = {
        "schema_version": 1,
        "status": "needs_ocr_or_review" if pending_ocr or empty_pages else "prepared_pending_review",
        "ready_for_wiki_generation": False,
        "input_dir": str(input_dir), "source_count": len(sources),
        "page_count": len(pages), "table_count": len(tables),
        "table_candidate_count": len(table_candidates),
        "review_page_count": len(reviews), "pending_ocr_page_count": pending_ocr,
        "empty_page_count": empty_pages, "dataset_notes": notes_record, "sources": sources,
        "notes": ["Original PDFs were not modified or deleted.",
                  "tables.jsonl contains only heuristic structured candidates; table_candidates.jsonl keeps false positives and complex layouts for audit.",
                  "Search text is still pending human review of flagged OCR/table pages; do not use this snapshot as the final Wiki-generation input yet."],
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "README.md").write_text(
        "# Prepared corpus\n\n"
        "Derived from the original `data/` PDFs. Original source files remain untouched.\n\n"
        "- `sources.jsonl`: source hashes, PDF outline and per-page status.\n"
        "- `pages.jsonl`: page text, layout blocks, OCR output and quality flags.\n"
        "- `tables.jsonl`: heuristic structured-table candidates with page locators.\n"
        "- `table_candidates.jsonl`: all detected regions, including rejected false positives and complex layouts.\n"
        "- `review.csv`: pages needing OCR or visual/manual review.\n"
        "- `exclusions.jsonl`: pages excluded from search only if both text layers are empty.\n"
        "- `manifest.json`: build summary, source identities and readiness status.\n\n"
        "Do not treat this dataset as Wiki-ready while `manifest.json` is not `ready`.\n",
        encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ocr-language", default="chi_sim+eng")
    parser.add_argument("--ocr-scale", type=float, default=2.0)
    args = parser.parse_args()
    result = prepare(args.input, args.output, args.ocr_language, args.ocr_scale)
    print(json.dumps({k: result[k] for k in (
        "status", "source_count", "page_count", "table_count", "review_page_count",
        "pending_ocr_page_count", "empty_page_count")}, ensure_ascii=False, indent=2))
    print(f"Prepared data saved at: {args.output}")


if __name__ == "__main__":
    main()
