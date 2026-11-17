"""Compile a corpus into three layers with resumable, validated LLM calls."""

import copy
import os
from pathlib import Path

from .corpus import load_corpus, stats
from .files import canonical, digest, implementation_hash, now, read_json, write_json
from .llm import Client, PROMPTS
from .planning import include_cited_boundary_pages, make_tree, make_units, toc_context, validate_toc
from .registry import Registry
from .render import render
from .schema import cite_pages, validate_joint, validate_synthesis
from .validation import validate_build


def synthesize(client, task, evidence, label, **context):
    """Bounded evidence reduction; never drop later sources when a topic grows."""
    # Reserve room for registry, instructions and optional repair conversation.
    budget = (client.config["max_input_chars"] - len(canonical(context)) - 16000) // 2
    if budget < 3000:
        raise ValueError("主题注册表/上下文过大，请提高max_input_chars")
    groups, group, size = [], [], 0
    for paragraph in evidence:
        length = len(canonical(paragraph))
        if length > budget:
            raise ValueError("单个证据段落超过综合预算")
        if group and size + length > budget:
            groups.append(group)
            group, size = [], 0
        group.append(paragraph)
        size += length
    if group:
        groups.append(group)
    if len(groups) > 1:
        merged = []
        for i, subset in enumerate(groups):
            result = synthesize(client, task, subset, f"{label}/partial-{i+1}", **context)
            merged.extend(result["paragraphs"])
        if len(canonical(merged)) >= len(canonical(evidence)):
            raise ValueError("综合未缩短输入，停止递归；请提高上下文预算")
        return synthesize(client, task, merged, label + "/merge", **context)
    payload = {"evidence": evidence, "partial": "/partial-" in label, **context}
    topic_ids = {t["id"] for t in context.get("registry", [])} if task == "topic" else None
    return client.ask(task, payload, lambda value: validate_synthesis(
        value, evidence, topic_ids, context.get("topic_id")), label)


def build(input_dir, output, provider, config, selected=(), resume=False, key=None, transport=None):
    docs, fingerprint = load_corpus(input_dir, selected)
    output = Path(output).resolve()
    signature = digest({"input": fingerprint, "provider": provider, "config": config,
                        "prompts": {p.name: p.read_text() for p in sorted(PROMPTS.glob("*.md"))}})
    if output.exists():
        if not resume or not (output / "run.json").exists():
            raise ValueError("输出目录已存在；使用新目录或明确指定--resume")
        run = read_json(output / "run.json")
        if run["signature"] != signature:
            raise ValueError("输入、选择范围、配置或Prompt已改变；请使用新的输出目录")
    else:
        if resume:
            raise ValueError("--resume需要已有运行目录")
        output.mkdir(parents=True)
        run = {"created_at": now(), "signature": signature, "provider": provider, "config": config,
               "input_dir": str(Path(input_dir).resolve()), "input_fingerprint": fingerprint,
               "stats": stats(docs), "semantic_quality": "not_evaluated"}
    lock = output / ".build.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ValueError("该输出目录已有构建锁；确认无进程运行后才可删除残留.build.lock") from None
    os.close(fd)
    client = Client(output, provider, config, key, transport)
    registry, compiled = Registry(), {}
    run.pop("error_type", None)
    run.pop("finished_at", None)
    run.update(status="running", updated_at=now(), implementation_sha256=implementation_hash())
    write_json(output / "run.json", run)
    try:
        source_hashes = {}
        for sid, doc in docs.items():
            path = output / "wiki/sources" / f"{sid}.json"
            write_json(path, doc["pages"])
            source_hashes[sid] = digest(path.read_bytes())
        write_json(output / "state/source-hashes.json", source_hashes)
        for position, (sid, doc) in enumerate(docs.items(), 1):
            print(f"文档 {position}/{len(docs)}：{doc['source']['source_name']}", flush=True)
            toc = client.ask("toc", toc_context(doc), lambda v: validate_toc(v, len(doc["pages"])), f"{sid}/toc")
            tree = make_tree(toc, len(doc["pages"]))
            units = make_units(doc, tree, config["unit_max_chars"], config["unit_max_pages"])
            result = {"source": doc["source"], "tree": tree, "section_paragraphs": {}, "units": []}
            write_json(output / "state/plans" / f"{sid}.json", {"tree": tree,
                       "units": [{"unit_id": u["unit_id"], "pages": [p["page"] for p in u["pages"]]} for u in units]})
            for unit in units:
                payload = {"source_id": sid, "source_name": doc["source"]["source_name"],
                           "sections": [{k: x[k] for k in ("id", "title", "start_page", "end_page")}
                                        for x in tree if any(p["section_id"] == x["id"] for p in unit["pages"])],
                           "pages": unit["pages"], "registry": registry.catalog(),
                           "max_topics_per_unit": config["max_topics_per_unit"]}

                def validate(value):
                    validate_joint(value, payload)
                    # Validate the entire registration transaction before caching it.
                    probe = copy.deepcopy(registry)
                    for action in value["topics"]:
                        probe.register(action, sid, unit["unit_id"])
                    return value

                joint = client.ask("joint", payload, validate, f"{sid}/{unit['unit_id']}")
                for summary in joint["summaries"]:
                    result["section_paragraphs"].setdefault(summary["section_id"], []).extend(
                        cite_pages(summary["paragraphs"], sid))
                tids = [registry.register(a, sid, unit["unit_id"]) for a in joint["topics"]]
                result["units"].append({"id": unit["unit_id"], "page_numbers": [p["page"] for p in unit["pages"]],
                                        "topic_ids": tids})
                write_json(output / "state/topics.json", registry.topics)
                run.update(current_document=sid, current_unit=unit["unit_id"], updated_at=now(), usage=client.usage())
                write_json(output / "run.json", run)
            include_cited_boundary_pages(result["tree"], result["section_paragraphs"])
            evidence = [p for group in result["section_paragraphs"].values() for p in group]
            result["overview"] = synthesize(client, "overview", evidence, f"{sid}/overview",
                                             source_name=doc["source"]["source_name"])
            compiled[sid] = result
            write_json(output / "state/compiled.json", compiled)
        for tid, topic in registry.topics.items():
            evidence = [p for contribution in topic["contributions"] for p in contribution["paragraphs"]]
            topic["result"] = synthesize(client, "topic", evidence, f"topic/{topic['title']}",
                                          topic_id=tid, title=topic["title"], registry=registry.catalog())
            write_json(output / "state/topics.json", registry.topics)
            run.update(current_topic=tid, updated_at=now(), usage=client.usage())
            write_json(output / "run.json", run)
        # Empty topic registries are valid for corpora without usable concepts.
        write_json(output / "state/topics.json", registry.topics)
        render(output, docs, compiled, registry, provider)
        validation = validate_build(output)
        write_json(output / "validation.json", validation)
        if not validation["structural_pass"]:
            raise ValueError("产物结构验证失败，参见validation.json")
        run.update(status="complete", finished_at=now(), metrics=validation["metrics"])
        return run
    except BaseException as exc:
        run.update(status="failed", error_type=type(exc).__name__, updated_at=now())
        raise
    finally:
        run["usage"] = client.usage()
        write_json(output / "run.json", run)
        lock.unlink(missing_ok=True)
