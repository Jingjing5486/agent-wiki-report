"""CLI for inspecting inputs, building wikis, and reading cited source pages."""

import argparse
import json
import sys
import tomllib
from pathlib import Path

from .corpus import load_corpus, stats
from .files import read_json, write_json
from .llm import DEFAULTS, load_key, validate_config
from .pipeline import build
from .planning import make_tree, make_units
from .validation import validate_build


def parser():
    p = argparse.ArgumentParser(description="SearchWiki三层POC：documents / topics / sources")
    commands = p.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="本地数据检查和分批估算，不调用API")
    generate = commands.add_parser("build", help="生成独立的三层Wiki")
    for sub in (inspect, generate):
        sub.add_argument("--input", type=Path, default=Path("../../data-prepared/demo-v2"))
        sub.add_argument("--source", action="append", default=[], help="可重复传入01–10或source_id；默认全部")
        sub.add_argument("--config", type=Path)
    generate.add_argument("--output", type=Path, required=True)
    generate.add_argument("--provider", choices=["offline", "deepseek"], default="offline")
    generate.add_argument("--env-file", type=Path, help="显式指定密钥文件，不自动读取兄弟项目.env")
    generate.add_argument("--resume", action="store_true")
    check = commands.add_parser("validate", help="验证完整构建的页覆盖、引用和Markdown链接")
    check.add_argument("output", type=Path)
    read = commands.add_parser("read-page", help="按来源与物理页码读取JSON中的原始证据")
    read.add_argument("output", type=Path)
    read.add_argument("--source", required=True)
    read.add_argument("--page", type=int, required=True)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command in {"inspect", "build"}:
            config = dict(DEFAULTS)
            if args.config:
                config.update(tomllib.loads(args.config.read_text()))
            validate_config(config)
        if args.command == "inspect":
            docs, fingerprint = load_corpus(args.input, args.source)
            units = 0
            for doc in docs.values():
                tree = make_tree({"sections": [{"title": "all", "level": 1, "start_page": 1}]}, len(doc["pages"]))
                units += len(make_units(doc, tree, config["unit_max_chars"], config["unit_max_pages"]))
            result = {**stats(docs), "input_fingerprint": fingerprint, "joint_compilation_calls": units,
                      "toc_and_overview_calls_minimum": len(docs) * 2,
                      "note": "另需主题综合调用；不含结构修复和长摘要归并。offline不产生API费用。"}
        elif args.command == "build":
            key = load_key(args.env_file) if args.provider == "deepseek" else None
            result = build(args.input, args.output, args.provider, config, args.source, args.resume, key)
        elif args.command == "validate":
            result = validate_build(args.output)
            write_json(args.output / "validation.json", result)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["structural_pass"] else 1
        else:
            paths = list((args.output / "wiki/sources").glob("*.json"))
            result = None
            for path in paths:
                rows = read_json(path)
                if rows and (rows[0]["source_id"] == args.source or
                             rows[0]["source_name"].startswith(args.source + "_")):
                    result = next((p for p in rows if p["page"] == args.page), None)
                    break
            if result is None:
                raise ValueError("未找到来源或物理页码")
            result = {k: result[k] for k in ("source_id", "source_name", "page", "search_text", "quality_flags")}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        # Known validation errors are controlled; never dump request headers or credential files.
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
