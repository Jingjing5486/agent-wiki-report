"""Read-only MD/TXT/PDF input, with source hashes and precise text offsets."""

from pathlib import Path

from .storage import canonical, digest, new_directory, now, read_json, write_json


def page_selection(spec: str | None, total: int) -> list[int]:
    if not spec:
        return list(range(1, total + 1))
    pages = set()
    for part in spec.split(","):
        pair = part.strip().split("-")
        if len(pair) not in (1, 2):
            raise ValueError("PDF 页码格式应为 1-3,5（从 1 开始）")
        start = int(pair[0])
        end = int(pair[-1])
        if not 1 <= start <= end <= total:
            raise ValueError(f"PDF 页码 {part} 超出 1..{total}")
        pages.update(range(start, end + 1))
    return sorted(pages)


def read_units(path: Path, pdf_pages: str | None):
    if path.suffix.lower() != ".pdf":
        return [{"page": None, "text": path.read_text(encoding="utf-8")}], None
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ValueError('PDF 需要可选依赖：python3 -m pip install -e ".[pdf]"') from exc
    reader = PdfReader(str(path))
    selected = page_selection(pdf_pages, len(reader.pages))
    units = [{"page": n, "text": reader.pages[n - 1].extract_text() or "", "method": "pypdf"}
             for n in selected]
    return units, len(reader.pages)


def prepare(input_dir: Path, output: Path, patterns: list[str] | None = None,
            pdf_pages: str | None = None, chunk_chars: int = 1800) -> dict:
    root = input_dir.resolve()
    if not root.is_dir():
        raise ValueError(f"输入目录不存在：{root}")
    if output.resolve().is_relative_to(root):
        raise ValueError("输出目录必须在输入目录之外，避免把历史产物重新当成资料")
    if chunk_chars < 100:
        raise ValueError("chunk_chars 至少为 100")
    selected = sorted({p for pattern in (patterns or ["**/*.md", "**/*.txt", "**/*.pdf"])
                       for p in root.glob(pattern) if p.is_file()})
    if not selected:
        raise ValueError("没有匹配的资料")
    sources, evidence = [], []
    for path in selected:
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError(f"不读取越界或符号链接资料：{path.name}")
        if path.suffix.lower() not in {".md", ".txt", ".pdf"}:
            raise ValueError(f"不支持的格式：{path.name}")
        raw = path.read_bytes()
        relative = path.relative_to(root).as_posix()
        source_id = "d-" + digest(relative.encode())[:16]
        units, total_pages = read_units(path, pdf_pages)
        item = {"source_id": source_id, "path": relative, "sha256": digest(raw),
                "total_pages": total_pages, "units": units,
                "selected_pages": [u["page"] for u in units if u["page"]],
                "warnings": []}
        if total_pages and len(units) < total_pages:
            item["warnings"].append("partial_document: 只选择了指定 PDF 页码")
        for unit in units:
            text = unit["text"]
            if not text.strip():
                item["warnings"].append(f"no_text: page={unit['page']}，需检查空白页或 OCR")
                continue
            start = 0
            while start < len(text):
                end = min(start + chunk_chars, len(text))
                if end < len(text):
                    boundary = text.rfind("\n", start + chunk_chars // 2, end)
                    if boundary >= 0:
                        end = boundary + 1
                snippet = text[start:end]
                if snippet.strip():
                    locator = {"page": unit["page"], "start_char": start, "end_char": end}
                    evidence_id = "e-" + digest(canonical([source_id, item["sha256"], locator]))[:20]
                    evidence.append({"evidence_id": evidence_id, "source_id": source_id,
                                     "source_sha256": item["sha256"],
                                     "locator": locator, "text": snippet})
                start = end
        sources.append(item)
    payload = {"schema_version": 1, "sources": sources, "evidence": evidence}
    result = {**payload, "corpus_id": digest(canonical(payload)), "created_at": now(),
              "input_root": str(root), "selection": {"patterns": patterns, "pdf_pages": pdf_pages},
              "chunk_chars": chunk_chars}
    new_directory(output)
    write_json(output / "corpus.json", result)
    write_json(output / "manifest.json", {
        "corpus_id": result["corpus_id"], "source_count": len(sources),
        "evidence_count": len(evidence), "selection": result["selection"],
        "sources": [{k: v for k, v in s.items() if k != "units"} for s in sources],
        "notes": "PDF 仅提取文字层，不解析图表、不做 OCR；字符切片仅为 demo 基线。"})
    return result


def load_corpus(path: Path) -> dict:
    corpus = read_json(path / "corpus.json" if path.is_dir() else path)
    payload = {k: corpus[k] for k in ("schema_version", "sources", "evidence")}
    if digest(canonical(payload)) != corpus["corpus_id"]:
        raise ValueError("资料快照校验失败：corpus 内容已变化")
    source_map = {s["source_id"]: s for s in corpus["sources"]}
    seen = set()
    for e in corpus["evidence"]:
        if e["evidence_id"] in seen:
            raise ValueError("重复 evidence_id")
        seen.add(e["evidence_id"])
        source = source_map[e["source_id"]]
        loc = e["locator"]
        unit = next(u for u in source["units"] if u["page"] == loc["page"])
        if (e["source_sha256"] != source["sha256"] or
                unit["text"][loc["start_char"]:loc["end_char"]] != e["text"]):
            raise ValueError("证据与提取文本不匹配")
    return corpus
