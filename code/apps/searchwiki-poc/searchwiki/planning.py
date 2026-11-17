"""TOC tree, physical-page ownership, and bounded compilation units."""

import re


def meaningful_outline(source):
    return [x for x in source.get("outline", []) if
            1 <= x.get("page", 0) <= source["page_count"] and
            not re.fullmatch(r"(?:Default Section|Slide\s*\d+|幻灯片\s*\d+)",
                             x.get("title", ""), re.I)]


def toc_context(doc):
    # Signals intentionally compact; full page text is consumed in joint compilation.
    return {"source_id": doc["source"]["source_id"],
            "source_name": doc["source"]["source_name"],
            "page_count": len(doc["pages"]),
            "outline": meaningful_outline(doc["source"]),
            "opening_pages": [{"page": p["page"], "text": p["search_text"][:5000]}
                              for p in doc["pages"][:3]],
            "page_signals": [{"page": p["page"], "opening": p["search_text"][:180],
                              "headings": [h["text"][:100] for h in
                                           p.get("heading_candidates", [])[:4]]}
                             for p in doc["pages"]]}


def validate_toc(value, count):
    entries = value.get("sections") if isinstance(value, dict) else None
    if not isinstance(entries, list) or not entries or len(entries) > 50:
        raise ValueError("sections 必须包含1–50个目录条目")
    last_page, last_level = 0, 0
    for item in entries:
        if not isinstance(item, dict) or not isinstance(item.get("title"), str) or not item["title"].strip():
            raise ValueError("目录标题不能为空")
        start, level = item.get("start_page"), item.get("level")
        if type(start) is not int or not 1 <= start <= count or start < last_page:
            raise ValueError("目录必须按有效物理页码排序")
        if type(level) is not int or not 1 <= level <= 4 or level > last_level + 1:
            raise ValueError("目录深度必须1起始且不得跳级，最多4级")
        last_page, last_level = start, level
    return value


def make_tree(value, page_count):
    validate_toc(value, page_count)
    # Several real subsections can start on one physical page. At page granularity
    # consolidate same-page/same-level headings instead of inventing empty ranges.
    by_page = {}
    for item in value["sections"]:
        titles = by_page.setdefault(item["start_page"], {}).setdefault(item["level"], [])
        if item["title"] not in titles:
            titles.append(item["title"])
    entries = [{"title": " / ".join(titles), "level": level, "start_page": start}
               for start, levels in by_page.items() for level, titles in sorted(levels.items())]
    if entries[0]["start_page"] > 1:
        entries.insert(0, {"title": "前置资料", "level": 1, "start_page": 1})
    tree, stack = [], []
    for i, entry in enumerate(entries):
        while stack and stack[-1]["level"] >= entry["level"]:
            stack.pop()
        end = next((x["start_page"] - 1 for x in entries[i + 1:]
                    if x["level"] <= entry["level"]), page_count)
        if end < entry["start_page"]:
            raise ValueError("目录章节范围为空")
        sid = f"s{i + 1:03d}"
        node = {"id": sid, "title": entry["title"].strip(), "level": entry["level"],
                "start_page": entry["start_page"], "end_page": end,
                "parent_id": stack[-1]["id"] if stack else None,
                "path": (stack[-1]["path"] + "/" if stack else "sections/") + sid}
        tree.append(node)
        stack.append(node)
    return tree


def owner(tree, page):
    return max((x for x in tree if x["start_page"] <= page <= x["end_page"]),
               key=lambda x: x["level"])["id"]


def include_cited_boundary_pages(tree, summaries):
    """Keep the initial page partition, but allow cited shared boundary pages in output."""
    by_id = {s["id"]: s for s in tree}
    for node in reversed(tree):
        cited = [c["page"] for p in summaries.get(node["id"], []) for c in p["citations"]]
        child_ends = [s["end_page"] for s in tree if s["parent_id"] == node["id"]]
        end = max([node["end_page"], *cited, *child_ends])
        if end > node["end_page"]:
            node["planned_end_page"] = node["end_page"]
            node["end_page"] = end
            node["range_note"] = "与相邻章节共享边界页；按实际摘要引用扩展"


def make_units(doc, tree, max_chars, max_pages):
    units, current, size = [], [], 0
    for page in doc["pages"]:
        length = len(page["search_text"])
        if length > max_chars:
            raise ValueError(f"物理页{page['page']}超过unit_max_chars，请提高配置；没有截断正文")
        if current and (size + length > max_chars or len(current) >= max_pages):
            units.append(current)
            current, size = [], 0
        current.append({"page": page["page"], "section_id": owner(tree, page["page"]),
                        "text": page["search_text"], "quality_flags": page.get("quality_flags", [])})
        size += length
    if current:
        units.append(current)
    return [{"unit_id": f"u{i + 1:03d}", "pages": rows} for i, rows in enumerate(units)]
