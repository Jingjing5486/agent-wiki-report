#!/usr/bin/env python3
"""Create a deterministic inventory for reviewing staged llmwiki candidates."""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path


def norm(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    candidate_dir = args.project / ".llmwiki" / "candidates"
    candidates = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(candidate_dir.glob("*.json"))]
    source_names = {
        row["file"]: row.get("source_name", row["file"])
        for row in json.loads((args.project / "source-manifest.json").read_text(encoding="utf-8"))["source_files"]
    }

    def yaml_field(body: str, field: str) -> str:
        frontmatter = body.split("---", 2)[1] if body.count("---") >= 2 else ""
        match = re.search(rf"(?m)^{re.escape(field)}:\s*([^\n]+)", frontmatter)
        return match.group(1).strip().strip('"') if match else "unspecified"

    source_counts: Counter[str] = Counter()
    source_titles: dict[str, list[str]] = defaultdict(list)
    pdf_counts: Counter[str] = Counter()
    pdf_titles: dict[str, list[str]] = defaultdict(list)
    kind_counts: Counter[str] = Counter()
    provenance_counts: Counter[str] = Counter()
    confidence_counts: Counter[str] = Counter()
    exact_titles: dict[str, list[str]] = defaultdict(list)
    candidates_by_id: dict[str, dict] = {}
    for item in candidates:
        title = item.get("title") or item.get("slug") or item["id"]
        candidates_by_id[item["id"]] = item
        exact_titles[norm(title)].append(title)
        kind_counts[yaml_field(item.get("body", ""), "kind")] += 1
        provenance_counts[yaml_field(item.get("body", ""), "provenanceState")] += 1
        confidence = item.get("confidence")
        confidence_counts["missing" if confidence is None else ("<0.5" if confidence < .5 else "0.5-0.79" if confidence < .8 else ">=0.8")] += 1
        for source in item.get("sources", []):
            source_counts[source] += 1
            source_titles[source].append(title)
            original_pdf = source_names.get(source, source)
            pdf_counts[original_pdf] += 1
            pdf_titles[original_pdf].append(title)

    exact_duplicate_groups = [sorted(set(values)) for values in exact_titles.values() if len(set(values)) > 1]
    near_pairs: list[tuple[float, str, str]] = []
    items = sorted(((item.get("title", ""), item) for item in candidates), key=lambda pair: pair[0])
    for index, (left, left_item) in enumerate(items):
        left_norm = norm(left)
        if len(left_norm) < 5:
            continue
        for right, right_item in items[index + 1 :]:
            right_norm = norm(right)
            if len(right_norm) < 5:
                continue
            score = SequenceMatcher(None, left_norm, right_norm).ratio()
            if score >= .78:
                near_pairs.append((score, left, right))
    near_pairs.sort(reverse=True)

    policy_sources = [s for s in source_counts if "国务院公报" in source_names.get(s, "")]
    policy_titles = sorted({title for source in policy_sources for title in source_titles[source]})
    source_rows = [
        {"file": source, "source_name": source_names.get(source, source), "candidate_count": count,
         "titles": sorted(set(source_titles[source]))}
        for source, count in source_counts.most_common()
    ]
    report = {
        "candidate_count": len(candidates),
        "distinct_source_count": len(source_counts),
        "distinct_original_pdf_count": len(pdf_counts),
        "candidate_kind_counts": dict(kind_counts),
        "provenance_state_counts": dict(provenance_counts),
        "confidence_buckets": dict(confidence_counts),
        "exact_duplicate_title_groups": exact_duplicate_groups,
        "near_duplicate_title_pairs": [
            {"similarity": round(score, 3), "titles": [left, right]} for score, left, right in near_pairs
        ],
        "policy_source_files": policy_sources,
        "policy_source_candidate_titles": policy_titles,
        "per_source": source_rows,
        "per_original_pdf": [
            {"source_name": source, "candidate_count": count, "titles": sorted(set(pdf_titles[source]))}
            for source, count in pdf_counts.most_common()
        ],
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "candidate-inventory.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# 候选页面全量盘点（自动统计，非审核结论）", "",
        f"- 候选页：{len(candidates)}；引用 Markdown 来源段：{len(source_counts)}；原始 PDF：{len(pdf_counts)}。",
        f"- 页面类型：{dict(kind_counts)}。来源合并状态：{dict(provenance_counts)}。置信度分桶：{dict(confidence_counts)}。",
        f"- 完全同名候选组：{len(exact_duplicate_groups)}。相似标题候选对（相似度 ≥0.78）：{len(near_pairs)}。标题相似只用于排查，不能自动判定重复；年份、披露类型或财务口径不同的页面应保留区分。",
        f"- 来自国务院公报的候选页：{len(policy_titles)}。这些页面是否属于目标知识范围需按语料范围决定，生成器不会自动替用户做范围裁决。",
        "", "## 相似标题候选对（最多 80 对，供人工比较）", "",
    ]
    for score, left, right in near_pairs[:80]:
        lines.append(f"- {score:.2f}：{left} ↔ {right}")
    if not near_pairs:
        lines.append("- 无")
    lines.extend(["", "## 按原始 PDF 统计", ""])
    for source, count in pdf_counts.most_common():
        lines.append(f"### {source} — {count} 个来源关联")
        lines.extend(f"- {title}" for title in sorted(set(pdf_titles[source])))
        lines.append("")
    (args.output / "candidate-inventory.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print(json.dumps({
        "candidate_count": len(candidates), "sources": len(source_counts), "original_pdfs": len(pdf_counts),
        "exact_duplicate_groups": len(exact_duplicate_groups), "near_duplicate_pairs": len(near_pairs),
        "policy_source_candidates": len(policy_titles), "output": str(args.output / "candidate-inventory.md"),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
