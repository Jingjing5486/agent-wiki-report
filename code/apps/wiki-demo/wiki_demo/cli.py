"""Commands can run from the repository with python3 -m wiki_demo."""

import argparse
import json
import sys
import tomllib
from pathlib import Path

from .generation import compare, generate, inspect_run
from .providers import load_env
from .sources import prepare


def parser():
    p = argparse.ArgumentParser(description="文档 → Wiki 生成与评估 demo")
    commands = p.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare", help="读取资料并保存有版本、位置的文本快照")
    prep.add_argument("--input", type=Path, required=True)
    prep.add_argument("--output", type=Path, required=True)
    prep.add_argument("--pattern", action="append", help="可重复；相对输入目录的 glob")
    prep.add_argument("--pdf-pages", help="例如 1-3,5；省略时提取全部页")
    prep.add_argument("--chunk-chars", type=int, default=1800)
    gen = commands.add_parser("generate", help="生成一套候选 Wiki")
    gen.add_argument("--corpus", type=Path, required=True)
    gen.add_argument("--output", type=Path, required=True)
    gen.add_argument("--provider", choices=["extractive", "deepseek"], required=True)
    gen.add_argument("--prompt", type=Path, default=Path("prompts/baseline.md"))
    gen.add_argument("--config", type=Path, default=Path("configs/deepseek.toml"))
    gen.add_argument("--env-file", type=Path, default=Path(".env"))
    gen.add_argument("--system-prompt", type=Path, help="默认读取 prompts/system.md")
    gen.add_argument("--single-page", action="store_true", help="要求且校验只生成一个内容页面")
    check = commands.add_parser("validate", help="复查候选 JSON 和证据结构；不判断语义正确性")
    check.add_argument("run", type=Path)
    cmp = commands.add_parser("compare", help="展示多个运行的结构统计")
    cmp.add_argument("runs", type=Path, nargs="+")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "prepare":
            corpus = prepare(args.input, args.output, args.pattern, args.pdf_pages, args.chunk_chars)
            print(f"已准备 {len(corpus['sources'])} 份资料，{len(corpus['evidence'])} 个证据片段：{args.output}")
            for source in corpus["sources"]:
                for warning in source["warnings"]:
                    print(f"提示：{source['path']} — {warning}")
            if not corpus["evidence"]:
                print("所选资料没有可提取文字，尚不能生成 Wiki。", file=sys.stderr)
                return 2
        elif args.command == "generate":
            config = {}
            if args.provider == "deepseek":
                load_env(args.env_file)
                config = tomllib.loads(args.config.read_text(encoding="utf-8"))
            generate(args.corpus, args.output, args.prompt, args.provider, config, args.system_prompt,
                     expected_pages=1 if args.single_page else None)
            print(f"候选已生成：{args.output / 'wiki' / 'index.md'}")
            print("结构检查通过；语义质量尚未评估。" if args.provider == "deepseek"
                  else "这是原文摘录演示，没有调用 LLM，不代表 Prompt 或综合效果。")
        elif args.command == "validate":
            result = inspect_run(args.run)
            print(json.dumps(result["validation"], ensure_ascii=False, indent=2))
            return 0 if result["validation"]["structural_pass"] else 2
        else:
            print(compare(args.runs), end="")
        return 0
    except (ValueError, OSError, KeyError, TypeError, StopIteration) as exc:
        if isinstance(exc, FileExistsError):
            print("错误：输出目录已存在，请更换目录名以保留之前的实验。", file=sys.stderr)
        elif isinstance(exc, (KeyError, TypeError, StopIteration)):
            print("错误：资料或运行文件结构无效。", file=sys.stderr)
        else:
            print(f"错误：{exc}", file=sys.stderr)
        return 2
