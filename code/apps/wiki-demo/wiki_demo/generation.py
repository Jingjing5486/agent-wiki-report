"""One-call generation baseline; writes only validated candidates to wiki/."""

import csv
import json
import time
from pathlib import Path

from .providers import check_deepseek, deepseek, extractive
from .sources import load_corpus
from .storage import canonical, digest, new_directory, now, read_json, write_json
from .validation import validate

PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts"


def load_system(path: Path | None = None) -> str:
    text = (path or PROMPT_DIR / "system.md").read_text(encoding="utf-8")
    if not text.strip():
        raise ValueError("System Prompt 不能为空")
    return text


def plain(text: str) -> str:
    # Model text is rendered as text, never as file paths, HTML, or executable markup.
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    for ch in ("\\", "`", "*", "_", "[", "]", "#", "|"):
        text = text.replace(ch, "\\" + ch)
    return text.replace("\r", " ").replace("\n", " ")


def render(output: Path, candidate: dict, corpus: dict, provider: str) -> None:
    wiki = output / "wiki"
    wiki.mkdir()
    mode = "离线原文摘录演示，未调用 LLM" if provider == "extractive" else "LLM 生成候选，语义质量待审阅"
    index = ["# Wiki 索引", "", mode, ""]
    source_numbers = {s["source_id"]: i for i, s in enumerate(corpus["sources"], 1)}
    evidence = {e["evidence_id"]: e for e in corpus["evidence"]}

    def citation(eid):
        e = evidence[eid]
        number = source_numbers[e["source_id"]]
        page = e["locator"]["page"]
        position = f"第{page}页" if page is not None else f"字符{e['locator']['start_char']}"
        return f"[S{number}·{position}](evidence.md#{eid})"

    for page in candidate["pages"]:
        index.append(f"- [{plain(page['title'])}]({page['slug']}.md)")
        lines = [f"# {plain(page['title'])}", "", mode, "", "[返回索引](index.md)", ""]
        for section in page["sections"]:
            lines.extend([f"## {plain(section['heading'])}", ""])
            for claim in section["claims"]:
                refs = " ".join(citation(eid) for eid in claim["evidence_ids"])
                lines.extend([f"**{claim['kind']}** · {plain(claim['text'])} {refs}", ""])
        lines.extend(["## 资料来源与解析范围", ""])
        for source in corpus["sources"]:
            number = source_numbers[source["source_id"]]
            selected = source["selected_pages"]
            scope = f"提取 {len(selected)}/{source['total_pages']} 个 PDF 页面的文字层" if source["total_pages"] else "读取全文文本"
            lines.append(f"- S{number}：{plain(source['path'])}；{scope}。")
            lines.extend(f"  - {plain(warning)}" for warning in source["warnings"])
        lines.extend(["", "引用指向提取文本快照；未核验图表版面，也未完成逐条人工事实核查。", ""])
        (wiki / f"{page['slug']}.md").write_text("\n".join(lines), encoding="utf-8")
    (wiki / "index.md").write_text("\n".join(index) + "\n", encoding="utf-8")
    source_map = {s["source_id"]: s for s in corpus["sources"]}
    lines = ["# 证据快照", "", "以下为本次选取的提取文本，PDF 页码为文件物理页码，从 1 开始。", ""]
    for e in corpus["evidence"]:
        source = source_map[e["source_id"]]
        lines.extend([f'<a id="{e["evidence_id"]}"></a>', f"## {e['evidence_id']}", "",
                      f"来源：{plain(source['path'])}；SHA-256：`{source['sha256']}`", "",
                      f"定位：`{json.dumps(e['locator'])}`", "", "> " + plain(e["text"]), ""])
    (wiki / "evidence.md").write_text("\n".join(lines), encoding="utf-8")


def review_template(output: Path, candidate: dict) -> None:
    with (output / "review.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["page", "claim_number", "claim", "evidence_ids", "supported", "reviewer_notes"])
        for page in candidate["pages"]:
            n = 0
            for section in page["sections"]:
                for claim in section["claims"]:
                    n += 1
                    # Prevent formula execution when a generated claim is opened in a spreadsheet.
                    text = claim["text"]
                    if text.lstrip().startswith(("=", "+", "-", "@")):
                        text = "'" + text
                    writer.writerow([page["slug"], n, text, " ".join(claim["evidence_ids"]), "", ""])


def generate(corpus_path: Path, output: Path, prompt_path: Path, provider: str, config: dict,
             system_path: Path | None = None, expected_pages: int | None = None) -> dict:
    corpus = load_corpus(corpus_path)
    if not corpus["evidence"]:
        raise ValueError("所选资料没有可用文本证据；请检查 OCR 或页码选择")
    prompt = prompt_path.read_text(encoding="utf-8")
    system = load_system(system_path)
    if not prompt.strip():
        raise ValueError("Prompt 不能为空")
    if expected_pages is not None:
        prompt += f"\n\n本次运行必须恰好生成 {expected_pages} 个内容页面，其他主题用章节组织。"
    user = prompt + "\n\n资料清单：\n" + json.dumps([
        {k: s[k] for k in ("source_id", "path", "sha256", "warnings")} for s in corpus["sources"]
    ], ensure_ascii=False) + "\n\n证据：\n" + json.dumps(corpus["evidence"], ensure_ascii=False)
    if provider not in {"deepseek", "extractive"}:
        raise ValueError("未知 provider")
    if provider == "deepseek":
        check_deepseek(config)
        if len(system) + len(user) > config["max_input_chars"]:
            raise ValueError("输入超过 max_input_chars；请选择更小资料范围，当前版不做静默截断")
    new_directory(output)
    start = time.monotonic()
    run = {"schema_version": 1, "created_at": now(), "status": "running", "provider": provider,
           "corpus_id": corpus["corpus_id"], "prompt_name": prompt_path.name,
           "prompt_sha256": digest(prompt.encode()), "system_sha256": digest(system.encode()),
           "input_sha256": digest(user.encode()), "input_chars": len(system) + len(user),
           "config": {k: config[k] for k in ("model", "base_url", "thinking", "temperature", "max_tokens",
                                              "timeout_seconds", "max_input_chars") if k in config}
                     if provider == "deepseek" else {},
           "semantic_quality": "not_evaluated", "expected_pages": expected_pages}
    write_json(output / "run.json", run)
    write_json(output / "corpus.json", corpus)
    write_json(output / "request.json", {"system": system, "user": user})
    (output / "system.md").write_text(system, encoding="utf-8")
    (output / "prompt.md").write_text(prompt, encoding="utf-8")
    try:
        raw, metadata = deepseek(system, user, config) if provider == "deepseek" else extractive(corpus)
        (output / "response.txt").write_text(raw, encoding="utf-8")
        run["response_metadata"] = metadata
        candidate = json.loads(raw)
        write_json(output / "candidate.json", candidate)
        result = validate(candidate, corpus, expected_pages)
        write_json(output / "validation.json", result)
        if not result["structural_pass"]:
            raise ValueError("候选未通过结构校验；查看 validation.json，未生成 wiki 目录")
        render(output, candidate, corpus, provider)
        review_template(output, candidate)
        run["status"] = "generated"
        return run
    except Exception as exc:
        run["status"] = "failed"
        # Store a type, not arbitrary server/library messages that may contain credentials.
        run["error_type"] = type(exc).__name__
        raise
    finally:
        run["elapsed_seconds"] = round(time.monotonic() - start, 3)
        write_json(output / "run.json", run)


def inspect_run(path: Path) -> dict:
    run = read_json(path / "run.json")
    corpus = load_corpus(path)
    candidate = read_json(path / "candidate.json")
    return {"run": run, "validation": validate(candidate, corpus, run.get("expected_pages"))}


def compare(paths: list[Path]) -> str:
    runs = [inspect_run(p) for p in paths]
    same_corpus = len({r["run"]["corpus_id"] for r in runs}) == 1
    lines = ["# 生成运行比较", "", f"相同资料快照：{'是' if same_corpus else '否，不能作受控比较'}", "",
             "仅比较运行与结构统计，不自动判定哪套 Wiki 更好。离线摘录不能用于比较 Prompt。", "",
             "| 运行 | 模式 | Prompt | 页数 | 主张数 | 引用来源数 | 结构检查 | 语义评估 |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for path, data in zip(paths, runs):
        r, v = data["run"], data["validation"]
        m = v["metrics"]
        lines.append(f"| {plain(path.name)} | {r['provider']} | {plain(r['prompt_name'])} | "
                     f"{m['page_count']} | {m['claim_count']} | {m['cited_source_count']} | "
                     f"{v['structural_pass']} | 未评估 |")
    configs = {canonical(r["run"]["config"]) for r in runs}
    if len(configs) > 1:
        lines.extend(["", "模型或运行配置不同；请结合 run.json 分析混杂因素。"])
    return "\n".join(lines) + "\n"
