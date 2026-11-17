"""Read existing extraction records without re-OCR or modifying the dataset."""

import re
from pathlib import Path

from .files import digest


def jsonl(path):
    import json
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def load_corpus(directory, selected=()):
    directory = Path(directory).resolve()
    sources = list(jsonl(directory / "sources.jsonl"))
    if selected:
        matched = {x for x in selected if any(s["source_id"] == x or
                   s["source_name"].startswith(x + "_") for s in sources)}
        if matched != set(selected):
            raise ValueError("未找到选择的来源编号/ID")
        sources = [s for s in sources if s["source_id"] in selected or
                   any(s["source_name"].startswith(x + "_") for x in selected)]
    if not sources:
        raise ValueError("没有来源")
    docs = {}
    for source in sorted(sources, key=lambda s: s["source_name"]):
        sid = source["source_id"]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,100}", sid) or sid in docs:
            raise ValueError("source_id 不安全或重复")
        docs[sid] = {"source": source, "pages": []}
    for page in jsonl(directory / "pages.jsonl"):
        if page["source_id"] not in docs:
            continue
        doc = docs[page["source_id"]]
        source = doc["source"]
        if (page["source_sha256"] != source["source_sha256"] or
                page["source_name"] != source["source_name"]):
            raise ValueError("页记录与来源身份不一致")
        if not isinstance(page.get("search_text"), str) or not page["search_text"].strip():
            raise ValueError("页文本为空，拒绝静默丢页")
        if type(page.get("page")) is not int or page.get("disposition") == "exclude":
            raise ValueError("无效页码或页记录被标记排除")
        doc["pages"].append(page)
    for doc in docs.values():
        doc["pages"].sort(key=lambda p: p["page"])
        if [p["page"] for p in doc["pages"]] != list(range(1, doc["source"]["page_count"] + 1)):
            raise ValueError("页码缺失或重复")
    fingerprint = digest({"sources": (directory / "sources.jsonl").read_bytes().hex(),
                          "pages_sha256": digest((directory / "pages.jsonl").read_bytes()),
                          "selected": sorted(docs)})
    return docs, fingerprint


def stats(docs):
    pages = [p for d in docs.values() for p in d["pages"]]
    return {"documents": len(docs), "pages": len(pages),
            "text_characters": sum(len(p["search_text"]) for p in pages),
            "flagged_pages": sum(bool(p.get("quality_flags")) for p in pages)}
