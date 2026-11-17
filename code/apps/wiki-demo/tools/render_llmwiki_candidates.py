#!/usr/bin/env python3
"""Render llmwiki review candidates as a browsable local Markdown preview.

This does not approve or alter candidates. It resolves page links for preview,
converts line citations to links back to the prepared source Markdown, and
records citation/link coverage diagnostics.
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path


WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
CITATION_RE = re.compile(r"\^\[([^\]]+)\]")
LINE_CITATION_RE = re.compile(r"^(.*\.md):(.+)$")
LINE_SPAN_RE = re.compile(r"^\s*(\d+)(?:\s*-\s*(\d+))?\s*$")
SOURCE_LINK_RE = re.compile(
    r"\[([^\]]*)\]\((?:\.\./)*sources/([^)#]+\.md)#L(\d+)(?:-L?(\d+))?\)"
)


def key(text: str) -> str:
    return unicodedata.normalize("NFKC", text).strip().casefold()


def normalized_link_key(text: str) -> str:
    """Match harmless spacing/punctuation variants without fuzzy matching."""
    return re.sub(r"[\W_]+", "", key(text), flags=re.UNICODE)


def frontmatter_aliases(body: str) -> list[str]:
    match = re.match(r"^---\s*\n([\s\S]*?)\n---(?:\n|$)", body)
    if not match:
        return []
    lines = match.group(1).splitlines()
    for index, line in enumerate(lines):
        if not line.startswith("aliases:"):
            continue
        inline = line.partition(":")[2].strip()
        if inline.startswith("[") and inline.endswith("]"):
            return [part.strip().strip("\"'") for part in inline[1:-1].split(",") if part.strip()]
        aliases: list[str] = []
        for alias_line in lines[index + 1 :]:
            item = re.match(r"^\s+-\s+(.+?)\s*$", alias_line)
            if item:
                aliases.append(item.group(1).strip().strip("\"'"))
            elif alias_line.strip() and not alias_line.startswith(" "):
                break
        return aliases
    return []


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def parse_line_spans(raw: str) -> list[tuple[str, list[tuple[int, int]]]] | None:
    citations: list[tuple[str, list[tuple[int, int]]]] = []
    current_file: str | None = None
    current_spans: list[tuple[int, int]] = []
    for item in raw.split(","):
        item = item.strip()
        parsed = LINE_CITATION_RE.match(item)
        if parsed:
            filename, range_text = parsed.groups()
            if current_file is not None:
                citations.append((current_file, current_spans))
            current_file, current_spans = filename, []
            match = LINE_SPAN_RE.match(range_text)
        else:
            match = LINE_SPAN_RE.match(item)
        if not match or current_file is None:
            return None
        first = int(match.group(1))
        last = int(match.group(2) or match.group(1))
        current_spans.append((min(first, last), max(first, last)))
    if current_file is not None:
        citations.append((current_file, current_spans))
    if not citations or any(not spans for _, spans in citations):
        return None
    return citations


def format_page_label(page_numbers: list[int]) -> str:
    if not page_numbers:
        return "PDF页码未映射"
    intervals: list[str] = []
    start = previous = page_numbers[0]
    for page in page_numbers[1:]:
        if page == previous + 1:
            previous = page
            continue
        intervals.append(str(start) if start == previous else f"{start}–{previous}")
        start = previous = page
    intervals.append(str(start) if start == previous else f"{start}–{previous}")
    return "PDF第" + "、".join(intervals) + "页"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True, help="llmwiki experiment project root")
    parser.add_argument("--output", type=Path, required=True, help="preview output directory")
    args = parser.parse_args()

    candidates_dir = args.project / ".llmwiki" / "candidates"
    sources_dir = args.project / "sources"
    crosswalk_path = args.project / "line-to-pdf-page.jsonl"
    candidates = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(candidates_dir.glob("*.json"))]
    crosswalk = read_jsonl(crosswalk_path)

    page_by_key: dict[str, dict] = {}
    normalized_page_keys: dict[str, dict[str, dict]] = {}
    for candidate in candidates:
        names = [candidate.get("title", ""), candidate.get("slug", ""), *frontmatter_aliases(candidate.get("body", ""))]
        for name in names:
            if not name:
                continue
            page_by_key[key(name)] = candidate
            normalized_page_keys.setdefault(normalized_link_key(name), {})[candidate["id"]] = candidate
        for alias in candidate.get("aliases", []):
            page_by_key[key(alias)] = candidate
            normalized_page_keys.setdefault(normalized_link_key(alias), {})[candidate["id"]] = candidate

    locator_rows: dict[str, list[dict]] = {}
    for row in crosswalk:
        locator_rows.setdefault(row["source_markdown"], []).append(row)

    pages_dir = args.output / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    citation_stats = Counter()
    link_stats = Counter()
    multi_source = []
    source_counts: Counter[str] = Counter()
    unresolved_links: Counter[str] = Counter()
    citation_examples = []
    missing_citation_sources: Counter[str] = Counter()
    unmapped_citation_ranges: Counter[str] = Counter()

    for candidate in candidates:
        title = candidate.get("title") or candidate.get("slug") or candidate["id"]
        slug = candidate.get("slug") or candidate["id"]
        body = candidate.get("body", "")
        for source in candidate.get("sources", []):
            source_counts[source] += 1
        if len(candidate.get("sources", [])) > 1:
            multi_source.append({"title": title, "sources": candidate["sources"]})

        def replace_citation(match: re.Match[str]) -> str:
            raw = match.group(1)
            citation_stats["total"] += 1
            parsed = parse_line_spans(raw)
            if not parsed:
                citation_stats["without_line_range"] += 1
                return f"`^[{raw}]`"
            groups = parsed
            citation_stats["source_references"] += len(groups)
            all_spans = [span for _, source_spans in groups for span in source_spans]
            first = min(span[0] for span in all_spans)
            last = max(span[1] for span in all_spans)
            rendered_links: list[str] = []
            for filename, spans in groups:
                if not spans:
                    continue
                first = min(start for start, _ in spans)
                last = max(end for _, end in spans)
                source_path = sources_dir / filename
                if not source_path.is_file():
                    citation_stats["missing_source_file"] += 1
                    missing_citation_sources[filename] += 1
                    rendered_links.append(f"`^[{filename}:{first}-{last}]`")
                    continue
                citation_stats["source_file_exists"] += 1
                page_numbers = sorted({
                    int(row["pdf_physical_page"])
                    for row in locator_rows.get(filename, [])
                    if any(int(row["line_start"]) <= end and int(row["line_end"]) >= start for start, end in spans)
                })
                if page_numbers:
                    citation_stats["mapped_to_pdf_page"] += 1
                    page_label = format_page_label(page_numbers)
                    if len(citation_examples) < 8:
                        citation_examples.append({"source": filename, "line_spans": spans, "pdf_pages": page_numbers})
                else:
                    citation_stats["unmapped_line_range"] += 1
                    unmapped_citation_ranges[f"{filename}:{first}-{last}"] += 1
                    page_label = f"source行 {first}–{last}"
                relative_source = f"../../sources/{filename}#L{first}-L{last}"
                rendered_links.append(f"[{page_label}]({relative_source})")
            citation_stats["with_line_range"] += 1
            return "; ".join(rendered_links) if rendered_links else f"`^[{raw}]`"

        body = CITATION_RE.sub(replace_citation, body)

        def replace_source_link(match: re.Match[str]) -> str:
            label, filename, first_s, last_s = match.groups()
            # Footnote citations were converted above into source Markdown
            # links. Do not count those second time as links that pre-existed
            # in the candidate body.
            if label.startswith("PDF第"):
                return match.group(0)
            first = int(first_s)
            last = int(last_s or first_s)
            citation_stats["source_links"] += 1
            source_path = sources_dir / filename
            if not source_path.is_file():
                citation_stats["source_link_missing_file"] += 1
                return match.group(0)
            citation_stats["source_link_file_exists"] += 1
            pages = sorted({
                int(row["pdf_physical_page"])
                for row in locator_rows.get(filename, [])
                if int(row["line_start"]) <= last and int(row["line_end"]) >= first
            })
            if not pages:
                citation_stats["source_link_unmapped"] += 1
                return match.group(0)
            citation_stats["source_link_mapped"] += 1
            href = f"../../sources/{filename}#L{first}-L{last}"
            return f"[{format_page_label(pages)}]({href})"

        body = SOURCE_LINK_RE.sub(replace_source_link, body)

        def replace_wikilink(match: re.Match[str]) -> str:
            raw = match.group(1).strip()
            target, sep, display = raw.partition("|")
            target_page, anchor_sep, anchor = target.partition("#")
            found = page_by_key.get(key(target_page)) or page_by_key.get(key(display if sep else target_page))
            if not found:
                matches = normalized_page_keys.get(normalized_link_key(target_page), {})
                if not matches and sep:
                    matches = normalized_page_keys.get(normalized_link_key(display), {})
                if len(matches) == 1:
                    found = next(iter(matches.values()))
                    link_stats["normalized"] += 1
                elif len(matches) > 1:
                    link_stats["ambiguous_normalized"] += 1
            if not found:
                source_name = target_page if target_page.endswith(".md") else f"{target_page}.md"
                if Path(source_name).name == source_name and (sources_dir / source_name).is_file():
                    link_stats["source_reference"] += 1
                    label = display.strip() if sep else target_page
                    anchor_suffix = f"#{anchor}" if anchor_sep else ""
                    return f"[来源：{label}](../../sources/{source_name}{anchor_suffix})"
                link_stats["unresolved"] += 1
                unresolved_links[raw] += 1
                return f"`[[{raw}]]`"
            if found.get("id") == candidate.get("id"):
                link_stats["self"] += 1
                return display or target
            link_stats["resolved"] += 1
            label = display.strip() if sep else target
            anchor_suffix = f"#{anchor}" if anchor_sep else ""
            return f"[{label}]({found.get('slug') or found['id']}.md{anchor_suffix})"

        body = WIKILINK_RE.sub(replace_wikilink, body)
        (pages_dir / f"{slug}.md").write_text(body.rstrip() + "\n", encoding="utf-8")

    candidates.sort(key=lambda item: (item.get("title", "").casefold(), item.get("id", "")))
    index_lines = [
        "# 批量 Wiki 编译候选预览",
        "",
        f"共 **{len(candidates)}** 个候选页面。页面从上游 llmwiki review JSON 渲染而来，链接和来源引用仅在预览层转换为可点击 Markdown；原始候选记录保留在 `.llmwiki/candidates/`。",
        "",
        "> 这些页面尚未批准进入正式 Wiki。页面准确性、主题范围、跨来源合并、数值、页码引用和链接均需人工审阅。",
        "",
        "## 页面目录",
        "",
    ]
    for candidate in candidates:
        title = candidate.get("title") or candidate.get("slug") or candidate["id"]
        slug = candidate.get("slug") or candidate["id"]
        summary = (candidate.get("summary") or "").replace("\n", " ").strip()
        sources = candidate.get("sources", [])
        confidence = candidate.get("confidence")
        confidence_text = f"；置信度 {confidence}" if confidence is not None else ""
        index_lines.append(f"- [{title}](pages/{slug}.md) — {summary}（来源 {len(sources)} 份{confidence_text}）")
    (args.output / "index.md").write_text("\n".join(index_lines) + "\n", encoding="utf-8")

    stats = {
        "candidate_pages": len(candidates),
        "distinct_source_files_referenced": len(source_counts),
        "multi_source_candidate_pages": multi_source,
        "source_file_reference_counts": dict(source_counts.most_common()),
        "citations": dict(citation_stats),
        "missing_citation_sources": dict(missing_citation_sources.most_common()),
        "unmapped_citation_ranges": dict(unmapped_citation_ranges.most_common()),
        "citation_examples": citation_examples,
        "links": dict(link_stats),
        "unresolved_link_targets": dict(unresolved_links.most_common(100)),
    }
    (args.output / "quality-summary.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report_lines = [
        "# 预览生成与引用/链接检查",
        "",
        f"- 候选页：{len(candidates)}",
        f"- 被引用的不同来源 Markdown：{len(source_counts)}",
        f"- 多来源候选页：{len(multi_source)}",
        f"- 行号引用标记：{citation_stats['with_line_range']}；解析出的来源目标：{citation_stats['source_references']}；映射到 PDF 物理页：{citation_stats['mapped_to_pdf_page']}；来源文件缺失：{citation_stats['missing_source_file']}；行号未映射：{citation_stats['unmapped_line_range']}",
        f"- 链接：解析到本批 Wiki 候选 {link_stats['resolved']}（其中经空格/标点/别名归一 {link_stats['normalized']}）；来源文件链接 {link_stats['source_reference']}；未解析 {link_stats['unresolved']}；歧义归一 {link_stats['ambiguous_normalized']}；自链接 {link_stats['self']}",
        "",
        "## 多来源页面",
        "",
    ]
    if multi_source:
        for row in multi_source:
            report_lines.append(f"- **{row['title']}** ← " + ", ".join(row["sources"]))
    else:
        report_lines.append("- 无")
    report_lines.extend(["", "## 无法映射或缺失的引用", ""])
    for filename, count in missing_citation_sources.most_common():
        report_lines.append(f"- 来源文件不存在：`{filename}`（{count} 个引用目标）")
    for citation, count in unmapped_citation_ranges.most_common():
        report_lines.append(f"- 行号无法对应 PDF 页面：`{citation}`（{count} 个引用目标）")
    if not missing_citation_sources and not unmapped_citation_ranges:
        report_lines.append("- 无")
    report_lines.extend(["", "## 未解析链接（频次最高 100 项）", ""])
    if unresolved_links:
        for target, count in unresolved_links.most_common(100):
            report_lines.append(f"- `{target}`：{count}")
    else:
        report_lines.append("- 无")
    report_lines.extend(["", "## 引用到 PDF 页码映射样例", ""])
    for example in citation_examples:
        ranges = ", ".join(str(start) if start == end else f"{start}-{end}" for start, end in example["line_spans"])
        report_lines.append(f"- `{example['source']}:{ranges}` → {format_page_label(example['pdf_pages'])}")
    (args.output / "quality-summary.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    print(f"Rendered {len(candidates)} candidates to {args.output}")
    print(f"Citation ranges: {citation_stats['with_line_range']}; PDF-page mapped: {citation_stats['mapped_to_pdf_page']}; links resolved: {link_stats['resolved']}; unresolved: {link_stats['unresolved']}")


if __name__ == "__main__":
    main()
