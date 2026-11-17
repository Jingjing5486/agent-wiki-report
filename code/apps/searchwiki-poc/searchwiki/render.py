"""Render typed Markdown with deterministic links and readable page evidence."""

import html
import json
import os
import re
from pathlib import Path

from .files import write_text


def plain(value):
    value = html.escape(str(value), quote=False)
    return re.sub(r"([\\`*_\[\]#|])", r"\\\1", value).replace("\n", " ")


def link(origin, target, label, anchor=""):
    path = Path(os.path.relpath(target, origin.parent)).as_posix()
    return f"[{plain(label)}]({path}{'#' + anchor if anchor else ''})"


def page(path, kind, title, lines, **metadata):
    front = {"type": kind, "title": title, **metadata}
    header = ["---", *[f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in front.items()],
              "---", "", f"# {plain(title)}", ""]
    write_text(path, "\n".join(header + lines) + "\n")


def paragraphs(origin, wiki, items, source_labels):
    lines = []
    for p in items:
        refs = " ".join(link(origin, wiki / "sources" / f"{c['source_id']}.md",
                             f"{source_labels[c['source_id']]}·p{c['page']}", f"page-{c['page']}")
                        for c in p["citations"])
        lines.extend([plain(p["text"]) + " " + refs, ""])
    return lines


def render_sources(wiki, docs):
    index = wiki / "sources/index.md"
    lines = ["逐页原文与JSON快照；PDF物理页码从1开始。", ""]
    for sid, doc in docs.items():
        path = wiki / "sources" / f"{sid}.md"
        source = doc["source"]
        body = [link(path, index, "返回来源目录"), "",
                link(path, path.with_suffix(".json"), "完整页级JSON（包含原有坐标与OCR字段）"), "",
                f"PDF SHA-256：`{source['source_sha256']}`", ""]
        for row in doc["pages"]:
            body.extend([f'<a id="page-{row["page"]}"></a>', f"## 物理页 {row['page']}", "",
                         "解析标记：" + plain(", ".join(row.get("quality_flags", [])) or "无"), "",
                         # Quote raw text as escaped HTML so source content cannot create accidental links.
                         "<pre>" + html.escape(row["search_text"]) + "</pre>", ""])
        page(path, "source", source["source_name"], body, source_id=sid)
        lines.append("- " + link(index, path, source["source_name"]))
    page(index, "catalog", "原始来源", lines)


def render(output, docs, compiled, registry, provider):
    wiki = Path(output) / "wiki"
    render_sources(wiki, docs)
    labels = {sid: d["source"]["source_name"].split("_")[0] for sid, d in docs.items()}
    topics = registry.topics
    mode = "离线结构演示：原文摘录和固定示例主题，未做语义生成。" if provider == "offline" else "DeepSeek生成的POC Wiki；沿用现有PDF/OCR解析。"
    doc_index = wiki / "documents/index.md"
    doc_lines = [mode, ""]
    for sid, result in compiled.items():
        base = wiki / "documents" / sid
        root, overview = base / "index.md", base / "overview.md"
        title = result["source"]["source_name"]
        doc_topics = [t for t in topics.values() if any(c["source_id"] == sid for c in t["contributions"])]
        links = ["- " + link(overview, wiki / "topics" / f"{t['id']}.md", t["title"]) for t in doc_topics]
        body = [link(overview, root, "文档章节目录"), "", mode, ""]
        body += paragraphs(overview, wiki, result["overview"]["paragraphs"], labels)
        body += ["## 跨文档主题", "", *links]
        page(overview, "document", title, body, source_id=sid, page_range=[1, result["source"]["page_count"]])
        root_lines = [link(root, doc_index, "全部文档"), "", link(root, overview, "文档概览"), "",
                      "## 章节", ""]
        by_id = {s["id"]: s for s in result["tree"]}
        for node in result["tree"]:
            node_root = base / node["path"] / "index.md"
            summary_path = node_root.with_name("summary.md")
            root_lines.append("  " * (node["level"] - 1) + "- " + link(root, node_root,
                              f"{node['title']}（p{node['start_page']}–{node['end_page']}）"))
            parent = base / by_id[node["parent_id"]]["path"] / "index.md" if node["parent_id"] else root
            children = [x for x in result["tree"] if x["parent_id"] == node["id"]]
            node_lines = [link(node_root, parent, "上级目录"), "", link(node_root, summary_path, "本节摘要"), ""]
            node_lines += ["- " + link(node_root, base / x["path"] / "index.md", x["title"]) for x in children]
            page(node_root, "catalog", node["title"], node_lines, source_id=sid,
                 page_range=[node["start_page"], node["end_page"]])
            # Parent sections include descendant summaries, each still carrying exact source citations.
            relevant = [x for x in result["tree"] if x["id"] == node["id"] or
                        x["path"].startswith(node["path"] + "/")]
            summary_lines = [link(summary_path, node_root, "本节目录"), ""]
            for x in relevant:
                items = result["section_paragraphs"].get(x["id"], [])
                if items:
                    summary_lines += [f"## {plain(x['title'])}", ""]
                    summary_lines += paragraphs(summary_path, wiki, items, labels)
            page(summary_path, "section", node["title"], summary_lines, source_id=sid,
                 page_range=[node["start_page"], node["end_page"]],
                 range_note=node.get("range_note", "按目录范围"))
        page(root, "catalog", title, root_lines, source_id=sid)
        doc_lines.append("- " + link(doc_index, root, title))
    page(doc_index, "catalog", "文档目录", doc_lines)
    topic_index = wiki / "topics/index.md"
    topic_lines = [mode, ""]
    for tid, topic in topics.items():
        path = wiki / "topics" / f"{tid}.md"
        source_ids = sorted({c["source_id"] for c in topic["contributions"]})
        body = [link(path, topic_index, "主题目录"), ""]
        body += paragraphs(path, wiki, topic["result"]["paragraphs"], labels)
        body += ["## 相关文档", "", *["- " + link(path, wiki / "documents" / sid / "index.md",
                  docs[sid]["source"]["source_name"]) for sid in source_ids], "", "## 相关主题", ""]
        body += ["- " + link(path, wiki / "topics" / f"{other}.md", topics[other]["title"])
                 for other in topic["result"].get("related_topic_ids", [])]
        page(path, "topic", topic["title"], body, topic_id=tid, aliases=topic["aliases"], source_ids=source_ids)
        topic_lines.append("- " + link(topic_index, path, f"{topic['title']}（{len(source_ids)}份来源）"))
    page(topic_index, "catalog", "跨文档主题", topic_lines)
    root = wiki / "index.md"
    page(root, "catalog", "SearchWiki POC", [mode, "", "从文档了解结构，从主题比较资料，再打开来源页核对证据。", "",
         "- " + link(root, doc_index, "文档与章节"), "- " + link(root, topic_index, "跨文档主题"),
         "- " + link(root, wiki / "sources/index.md", "逐页来源"),
         "- " + link(root, wiki / "log.md", "构建记录")])
    write_text(wiki / "log.md", "# 构建记录\n\n" + mode + "\n\n"
               f"文档：{len(docs)}；来源页：{sum(len(d['pages']) for d in docs.values())}；主题：{len(topics)}。\n\n"
               "[运行状态与用量](../run.json) · [目录和联合编译中间结果](../state/compiled.json)\n")
