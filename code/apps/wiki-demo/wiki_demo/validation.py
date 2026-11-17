"""Structural validation only. Citation existence does NOT imply factual support."""

import re


def validate(candidate, corpus: dict, expected_pages: int | None = None) -> dict:
    errors, warnings = [], []
    evidence = {e["evidence_id"]: e for e in corpus["evidence"]}
    used, slugs, texts = set(), set(), set()
    claim_count = 0

    def nonempty(value):
        return isinstance(value, str) and bool(value.strip())

    pages = candidate.get("pages") if isinstance(candidate, dict) else None
    if not isinstance(pages, list) or not pages:
        errors.append("pages 必须为非空列表")
        pages = []
    if expected_pages is not None and len(pages) != expected_pages:
        errors.append(f"要求恰好 {expected_pages} 个内容页面，实际为 {len(pages)}")
    for i, page in enumerate(pages):
        if not isinstance(page, dict):
            errors.append(f"page[{i}] 不是对象")
            continue
        slug = page.get("slug")
        if (not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", slug)
                or slug in {"index", "evidence"} or slug in slugs):
            errors.append(f"page[{i}] 的 slug 不安全、重复或保留")
        else:
            slugs.add(slug)
        if not nonempty(page.get("title")):
            errors.append(f"page[{i}] 缺少标题")
        sections = page.get("sections")
        if not isinstance(sections, list) or not sections:
            errors.append(f"page[{i}] 缺少 sections")
            continue
        for section in sections:
            if not isinstance(section, dict) or not nonempty(section.get("heading")):
                errors.append(f"page[{i}] 有无效章节")
                continue
            claims = section.get("claims")
            if not isinstance(claims, list) or not claims:
                errors.append(f"page[{i}] 章节缺少 claims")
                continue
            for claim in claims:
                claim_count += 1
                if not isinstance(claim, dict):
                    errors.append("claim 必须为对象")
                    continue
                text = claim.get("text")
                if not nonempty(text):
                    errors.append("claim 缺少文本")
                elif text in texts:
                    warnings.append("存在完全重复的主张文本，需人工检查")
                else:
                    texts.add(text)
                if claim.get("kind") not in {"fact", "inference", "uncertainty"}:
                    errors.append("claim.kind 必须为 fact/inference/uncertainty")
                refs = claim.get("evidence_ids")
                if not isinstance(refs, list) or not refs:
                    errors.append("每条主张必须关联至少一条证据")
                else:
                    for ref in refs:
                        if not isinstance(ref, str) or ref not in evidence:
                            errors.append("主张引用了不存在或无效的 evidence_id")
                        else:
                            used.add(ref)
    used_sources = {evidence[e]["source_id"] for e in used}
    return {"structural_pass": not errors, "errors": errors,
            "warnings": sorted(set(warnings)), "semantic_quality": "not_evaluated",
            "metrics": {"page_count": len(pages), "claim_count": claim_count,
                        "source_count": len(corpus["sources"]),
                        "cited_source_count": len(used_sources),
                        "evidence_count": len(evidence), "cited_evidence_count": len(used)},
            "uncited_evidence_ids": sorted(set(evidence) - used),
            "note": "引用数量不是知识覆盖率；结构通过不代表正确、完整或可上线。"}
