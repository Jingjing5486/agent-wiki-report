"""Deterministic extractive fixture; never presented as LLM topic discovery."""

from .planning import meaningful_outline


def respond(task, payload):
    if task == "toc":
        outline = meaningful_outline({"outline": payload["outline"], "page_count": payload["page_count"]})
        entries, seen = [], set()
        for x in outline:
            if x["page"] not in seen:
                entries.append({"title": x["title"], "level": 1, "start_page": x["page"]})
                seen.add(x["page"])
        return {"sections": entries or [{"title": "全文（离线结构演示）", "level": 1, "start_page": 1}]}
    if task == "joint":
        grouped = {}
        for p in payload["pages"]:
            grouped.setdefault(p["section_id"], []).append(p)
        summaries = [{"section_id": sid, "paragraphs": [
            {"text": "离线原文摘录：" + pages[0]["text"][:240], "pages": [pages[0]["page"]]}]}
            for sid, pages in grouped.items()]
        title = "离线跨文档摘录示例（非语义主题）"
        existing = next((t["id"] for t in payload["registry"] if t["title"] == title), None)
        return {"summaries": summaries, "topics": [{"title": title, "existing_topic_id": existing,
                "aliases": [], "paragraphs": [summaries[0]["paragraphs"][0]]}]}
    if task == "overview":
        return {"paragraphs": payload["evidence"][:4]}
    if task == "topic":
        # Retain one contribution from every source so reference coverage can be tested.
        chosen, seen = [], set()
        for p in payload["evidence"]:
            sources = {c["source_id"] for c in p["citations"]}
            if sources - seen:
                chosen.append(p)
                seen |= sources
        return {"paragraphs": chosen, "related_topic_ids": []}
    raise ValueError("未知离线任务")
