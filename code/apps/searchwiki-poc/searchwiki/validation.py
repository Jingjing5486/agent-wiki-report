"""Validate produced files, page coverage, and actual Markdown targets/anchors."""

import re
from pathlib import Path
from urllib.parse import unquote

from .files import read_json, digest
from .schema import citation_pairs


def validate_build(output):
    output = Path(output).resolve()
    state = read_json(output / "state/compiled.json")
    registry = read_json(output / "state/topics.json")
    source_hashes = read_json(output / "state/source-hashes.json")
    errors, source_pages = [], set()
    for sid, doc in state.items():
        path = output / "wiki/sources" / f"{sid}.json"
        rows = read_json(path)
        if digest(path.read_bytes()) != source_hashes[sid]:
            errors.append(f"来源快照被修改：{sid}")
        expected = list(range(1, doc["source"]["page_count"] + 1))
        if [p["page"] for p in rows] != expected:
            errors.append(f"来源页缺失/重复：{sid}")
        source_pages |= {(sid, p["page"]) for p in rows}
        coverage = [p for u in doc["units"] for p in u["page_numbers"]]
        if sorted(coverage) != expected:
            errors.append(f"编译单元页覆盖不完整：{sid}")
        for section in doc["tree"]:
            for s, n in citation_pairs(doc["section_paragraphs"].get(section["id"], [])):
                if s != sid or not section["start_page"] <= n <= section["end_page"]:
                    errors.append(f"章节引用越界：{sid}/{section['id']}")
        refs = citation_pairs(doc["overview"]["paragraphs"])
        if any(s != sid or n not in expected for s, n in refs):
            errors.append(f"文档概览引用越界：{sid}")
    cross_topics = 0
    for tid, topic in registry.items():
        refs = citation_pairs(topic["result"]["paragraphs"])
        evidence = [p for c in topic["contributions"] for p in c["paragraphs"]]
        allowed = citation_pairs(evidence)
        if not allowed <= source_pages or not refs <= allowed:
            errors.append(f"主题引用不属于输入证据：{tid}")
        sources = {s for s, _ in refs}
        if sources != {s for s, _ in allowed}:
            errors.append(f"主题遗漏贡献来源：{tid}")
        cross_topics += len(sources) > 1
        if any(t not in registry or t == tid for t in topic["result"].get("related_topic_ids", [])):
            errors.append(f"主题关联无效：{tid}")
    link_count = 0
    for path in (output / "wiki").rglob("*.md"):
        markdown = re.sub(r"<pre>.*?</pre>", "", path.read_text(), flags=re.S)
        for target in re.findall(r"(?<![!\\])\[[^\n]*?\]\(([^\s)]+)\)", markdown):
            link_count += 1
            name, _, anchor = target.partition("#")
            resolved = (path.parent / unquote(name)).resolve() if name else path
            if not resolved.is_relative_to(output) or not resolved.is_file():
                errors.append(f"失效或越界链接：{path.relative_to(output)} -> {target}")
            elif anchor and f'id="{anchor}"' not in resolved.read_text():
                errors.append(f"失效来源锚点：{target}")
    return {"structural_pass": not errors, "errors": errors, "semantic_quality": "not_evaluated",
            "metrics": {"documents": len(state), "source_pages": len(source_pages),
                        "sections": sum(len(d["tree"]) for d in state.values()),
                        "topics": len(registry), "cross_document_topics": cross_topics,
                        "markdown_links": link_count}}
