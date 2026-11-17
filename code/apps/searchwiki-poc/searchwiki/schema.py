"""Validate structure and citation membership; this is not a factuality judge."""


def require(condition, message):
    if not condition:
        raise ValueError(message)


def text(value):
    return isinstance(value, str) and bool(value.strip())


def paragraphs_with_pages(paragraphs, allowed):
    require(isinstance(paragraphs, list) and bool(paragraphs), "paragraphs必须非空")
    for paragraph in paragraphs:
        require(isinstance(paragraph, dict) and text(paragraph.get("text")), "段落text不能为空")
        pages = paragraph.get("pages")
        require(isinstance(pages, list) and bool(pages), "每段必须有pages")
        require(all(type(p) is int and p in allowed for p in pages), "引用超出本单元/章节页范围")


def validate_joint(value, payload):
    require(isinstance(value, dict), "输出必须是JSON对象")
    expected = {p["section_id"] for p in payload["pages"]}
    summaries, topics = value.get("summaries"), value.get("topics")
    require(isinstance(summaries, list), "缺少summaries列表")
    require(all(isinstance(s, dict) and isinstance(s.get("section_id"), str) for s in summaries),
            "每个摘要需要section_id")
    ids = [s["section_id"] for s in summaries]
    require(len(ids) == len(set(ids)) and set(ids) == expected, "summaries必须完整覆盖本单元章节且不得重复")
    for summary in summaries:
        allowed = {p["page"] for p in payload["pages"] if p["section_id"] == summary["section_id"]}
        section = next((s for s in payload.get("sections", []) if s["id"] == summary["section_id"]), None)
        if section is not None:
            # PDFs often start the next article below the end of the previous one.
            # Accept the single trailing boundary page ONLY when it is present in
            # this unit. The compiler expands the displayed range to match citations.
            boundary = section["end_page"] + 1
            if boundary in {p["page"] for p in payload["pages"]}:
                allowed.add(boundary)
        paragraphs_with_pages(summary.get("paragraphs"), allowed)
    require(isinstance(topics, list) and len(topics) <= payload["max_topics_per_unit"], "topics数量超过限制")
    # Some models emit explicit "no information in this unit" topic placeholders.
    # An empty citation list contributes no evidence: omit it and keep an audit note.
    # Out-of-range/non-empty citations still fail; section summaries remain mandatory.
    retained = []
    for topic in topics:
        if isinstance(topic, dict) and isinstance(topic.get("paragraphs"), list):
            original = topic["paragraphs"]
            keep = [p for p in original if not (isinstance(p, dict) and p.get("pages") == [])]
            if len(keep) != len(original):
                value.setdefault("normalization_notes", []).append(
                    f"未登记无页码证据的主题段落：{topic.get('title', '')}，{len(original)-len(keep)}段")
                if not keep:
                    continue
                topic["paragraphs"] = keep
        retained.append(topic)
    topics = value["topics"] = retained
    registry = {t["id"]: t for t in payload["registry"]}
    for topic in topics:
        require(isinstance(topic, dict) and text(topic.get("title")), "主题title不能为空")
        tid = topic.get("existing_topic_id")
        require(tid is None or (isinstance(tid, str) and tid in registry), "existing_topic_id不存在")
        aliases = topic.get("aliases", [])
        require(isinstance(aliases, list) and all(text(a) for a in aliases), "aliases必须是字符串列表")
        paragraphs_with_pages(topic.get("paragraphs"), {p["page"] for p in payload["pages"]})
    return value


def cite_pages(paragraphs, source_id):
    return [{"text": p["text"], "citations": [{"source_id": source_id, "page": n}
             for n in sorted(set(p["pages"]))]} for p in paragraphs]


def citation_pairs(paragraphs):
    return {(c["source_id"], c["page"]) for p in paragraphs for c in p["citations"]}


def validate_synthesis(value, evidence, registry=None, topic_id=None):
    require(isinstance(value, dict), "输出必须是JSON对象")
    paragraphs = value.get("paragraphs")
    require(isinstance(paragraphs, list) and bool(paragraphs), "缺少paragraphs")
    allowed = citation_pairs(evidence)
    for p in paragraphs:
        require(isinstance(p, dict) and text(p.get("text")), "段落text不能为空")
        refs = p.get("citations")
        require(isinstance(refs, list) and bool(refs), "每段必须带citations")
        for c in refs:
            require(isinstance(c, dict) and isinstance(c.get("source_id"), str) and
                    type(c.get("page")) is int, "引用格式错误")
            require((c["source_id"], c["page"]) in allowed, "引用不在提供的证据中")
    if registry is not None:
        related = value.get("related_topic_ids", [])
        require(isinstance(related, list) and len(related) <= 5 and
                all(isinstance(t, str) and t in registry and t != topic_id for t in related),
                "related_topic_ids包含无效主题")
        require({s for s, _ in citation_pairs(paragraphs)} == {s for s, _ in allowed},
                "主题综合必须保留每个输入来源的引用")
    return value
