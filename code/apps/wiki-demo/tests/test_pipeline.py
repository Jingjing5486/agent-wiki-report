import copy
import io
import json
import os
import re
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from wiki_demo.generation import compare, generate, inspect_run
from wiki_demo.providers import ProviderError, deepseek, load_env
from wiki_demo.sources import load_corpus, page_selection, prepare
from wiki_demo.storage import read_json
from wiki_demo.validation import validate


CONFIG = {"model": "deepseek-v4-pro", "base_url": "https://api.deepseek.com",
          "thinking": "disabled", "temperature": 0, "max_tokens": 8192,
          "timeout_seconds": 180, "max_input_chars": 60000}


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inputs = self.root / "input"
        self.inputs.mkdir()
        (self.inputs / "a.md").write_text("# 样例\n\n方法 A 的有效期为 10 分钟。\n", encoding="utf-8")
        (self.inputs / "b.txt").write_text("方法 B 依赖事件传递。", encoding="utf-8")
        self.corpus_path = self.root / "corpus"
        self.corpus = prepare(self.inputs, self.corpus_path)
        self.prompt = self.root / "prompt.md"
        self.prompt.write_text("基于证据生成主题页面。", encoding="utf-8")
        self.eid = self.corpus["evidence"][0]["evidence_id"]
        self.candidate = {"pages": [{"slug": "cache", "title": "缓存", "sections": [
            {"heading": "条件", "claims": [{"text": "方法 A 的有效期为 10 分钟。", "kind": "fact",
                                         "evidence_ids": [self.eid]}]}]}]}

    def test_snapshot_preserves_source_and_rejects_tampering(self):
        self.assertEqual(load_corpus(self.corpus_path)["corpus_id"], self.corpus["corpus_id"])
        path = self.corpus_path / "corpus.json"
        data = read_json(path)
        data["evidence"][0]["text"] = "篡改"
        path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "校验失败"):
            load_corpus(path)

    def test_version_changes_on_input_change(self):
        (self.inputs / "b.txt").write_text("修改后的方法 B", encoding="utf-8")
        updated = prepare(self.inputs, self.root / "new-corpus")
        self.assertNotEqual(updated["corpus_id"], self.corpus["corpus_id"])

    def test_chunks_preserve_all_text_and_offsets(self):
        text = "长段落。" * 151 + "\n第二段。" * 20
        (self.inputs / "long.txt").write_text(text, encoding="utf-8")
        corpus = prepare(self.inputs, self.root / "chunks", ["long.txt"], chunk_chars=100)
        self.assertEqual("".join(e["text"] for e in corpus["evidence"]), text)
        self.assertEqual(load_corpus(self.root / "chunks")["corpus_id"], corpus["corpus_id"])

    def test_output_cannot_pollute_sources_or_overwrite(self):
        with self.assertRaisesRegex(ValueError, "之外"):
            prepare(self.inputs, self.inputs / "output")
        with self.assertRaises(FileExistsError):
            prepare(self.inputs, self.corpus_path)

    def test_offline_end_to_end_and_links(self):
        run_dir = self.root / "offline"
        run = generate(self.corpus_path, run_dir, self.prompt, "extractive", {})
        self.assertEqual(run["status"], "generated")
        self.assertEqual(run["semantic_quality"], "not_evaluated")
        result = inspect_run(run_dir)
        self.assertTrue(result["validation"]["structural_pass"])
        self.assertEqual(result["validation"]["metrics"]["page_count"], 2)
        for page in (run_dir / "wiki").glob("*.md"):
            for target in re.findall(r'\]\(([^)]+)\)', page.read_text()):
                name, _, anchor = target.partition("#")
                dest = page.parent / name
                self.assertTrue(dest.exists())
                if anchor:
                    self.assertIn(f'id="{anchor}"', dest.read_text())
        self.assertIn("未评估", compare([run_dir]))

    def test_unknown_and_missing_citations_are_rejected(self):
        for refs in (["unknown"], [], [None]):
            self.candidate["pages"][0]["sections"][0]["claims"][0]["evidence_ids"] = refs
            self.assertFalse(validate(self.candidate, self.corpus)["structural_pass"])

    def test_path_traversal_reserved_and_duplicate_slugs(self):
        for slug in ("../outside", "/tmp/file", "index", "evidence", "a/b"):
            c = copy.deepcopy(self.candidate)
            c["pages"][0]["slug"] = slug
            self.assertFalse(validate(c, self.corpus)["structural_pass"])
        self.candidate["pages"].append(copy.deepcopy(self.candidate["pages"][0]))
        self.assertFalse(validate(self.candidate, self.corpus)["structural_pass"])

    def test_valid_citation_is_not_a_semantic_score(self):
        self.candidate["pages"][0]["sections"][0]["claims"][0]["text"] = "完全没有被证据支持的结论"
        report = validate(self.candidate, self.corpus)
        self.assertTrue(report["structural_pass"])
        self.assertEqual(report["semantic_quality"], "not_evaluated")

    def test_missing_key_and_budget_fail_before_call(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ProviderError, "缺少"):
                generate(self.corpus_path, self.root / "nokey", self.prompt, "deepseek", CONFIG)
        self.assertFalse((self.root / "nokey").exists())
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret"}):
            with patch("wiki_demo.providers.urllib.request.build_opener") as opener:
                with self.assertRaisesRegex(ValueError, "超过"):
                    generate(self.corpus_path, self.root / "large", self.prompt, "deepseek",
                             {**CONFIG, "max_input_chars": 10})
                opener.assert_not_called()
        self.assertFalse((self.root / "large").exists())

    def test_deepseek_request_and_complete_pipeline_with_fake_transport(self):
        response = {"model": "deepseek-v4-pro", "usage": {"total_tokens": 123},
                    "choices": [{"finish_reason": "stop", "message": {
                        "content": json.dumps(self.candidate)}}]}
        run_dir = self.root / "api-mocked"
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret"}):
            with patch("wiki_demo.providers.urllib.request.build_opener") as builder:
                builder.return_value.open.return_value = io.BytesIO(json.dumps(response).encode())
                run = generate(self.corpus_path, run_dir, self.prompt, "deepseek", CONFIG)
                request = builder.return_value.open.call_args.args[0]
                self.assertEqual(request.full_url, "https://api.deepseek.com/chat/completions")
                body = json.loads(request.data)
                self.assertEqual(body["model"], "deepseek-v4-pro")
                self.assertEqual(body["response_format"], {"type": "json_object"})
                self.assertEqual(body["thinking"], {"type": "disabled"})
                self.assertFalse(body["stream"])
        self.assertEqual(run["response_metadata"]["usage"]["total_tokens"], 123)
        for path in run_dir.rglob("*"):
            if path.is_file():
                self.assertNotIn("test-secret", path.read_text(encoding="utf-8-sig"))

    def test_truncated_blank_malformed_envelope(self):
        cases = [
            {"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]},
            {"choices": [{"finish_reason": "stop", "message": {"content": "   "}}]},
            {"choices": []},
        ]
        for response in cases:
            with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret"}):
                with patch("wiki_demo.providers.urllib.request.build_opener") as builder:
                    builder.return_value.open.return_value = io.BytesIO(json.dumps(response).encode())
                    with self.assertRaises(ProviderError):
                        deepseek("json", "input", CONFIG)

    def test_http_failure_does_not_log_response_secret(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret"}):
            with patch("wiki_demo.providers.urllib.request.build_opener") as builder:
                builder.return_value.open.side_effect = urllib.error.HTTPError(
                    "https://api.deepseek.com", 401, "test-secret", {}, None)
                with self.assertRaisesRegex(ProviderError, "HTTP 401") as caught:
                    deepseek("json", "input", CONFIG)
                self.assertNotIn("test-secret", str(caught.exception))

    def test_invalid_model_json_is_retained_without_wiki(self):
        for n, raw in enumerate(("not json", '{"pages":[]}')):
            path = self.root / f"invalid-{n}"
            with patch("wiki_demo.generation.extractive", return_value=(raw, {})):
                with self.assertRaises(ValueError):
                    generate(self.corpus_path, path, self.prompt, "extractive", {})
            self.assertEqual(read_json(path / "run.json")["status"], "failed")
            self.assertEqual((path / "response.txt").read_text(), raw)
            self.assertFalse((path / "wiki").exists())

    def test_env_never_executes_and_respects_existing_value(self):
        env = self.root / ".env"
        env.write_text("DEEPSEEK_API_KEY='$(touch nope)'\n", encoding="utf-8")
        with patch.dict(os.environ, {}, clear=True):
            load_env(env)
            self.assertEqual(os.environ["DEEPSEEK_API_KEY"], "$(touch nope)")
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "existing"}):
            load_env(env)
            self.assertEqual(os.environ["DEEPSEEK_API_KEY"], "existing")

    def test_page_selection(self):
        self.assertEqual(page_selection("1-2,4,2", 4), [1, 2, 4])
        for spec in ("0", "5", "3-1", "1-2-3"):
            with self.assertRaises(ValueError):
                page_selection(spec, 4)

    def test_single_page_is_enforced_and_persisted(self):
        self.assertTrue(validate(self.candidate, self.corpus, expected_pages=1)["structural_pass"])
        other = copy.deepcopy(self.candidate["pages"][0])
        other["slug"] = "second-topic"
        self.candidate["pages"].append(other)
        self.assertFalse(validate(self.candidate, self.corpus, expected_pages=1)["structural_pass"])
        path = self.root / "too-many-pages"
        with self.assertRaisesRegex(ValueError, "校验"):
            generate(self.corpus_path, path, self.prompt, "extractive", {}, expected_pages=1)
        self.assertEqual(read_json(path / "run.json")["expected_pages"], 1)
        self.assertFalse((path / "wiki").exists())

    def test_system_prompt_loaded_from_markdown_and_used_verbatim(self):
        system = self.root / "custom-system.md"
        system.write_text("自定义 system：只生成有引用的 JSON。", encoding="utf-8")
        path = self.root / "custom-system-run"
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret"}):
            with patch("wiki_demo.generation.deepseek", return_value=(json.dumps(self.candidate), {})) as call:
                generate(self.corpus_path, path, self.prompt, "deepseek", CONFIG, system, 1)
                self.assertEqual(call.call_args.args[0], system.read_text())
        self.assertEqual((path / "system.md").read_text(), system.read_text())
        self.assertEqual(read_json(path / "request.json")["system"], system.read_text())
        self.assertTrue(inspect_run(path)["validation"]["structural_pass"])
        system.write_text("", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "System Prompt"):
            generate(self.corpus_path, self.root / "empty-system", self.prompt, "extractive", {}, system)

    def test_scanned_or_blank_pdf_is_not_claimed_as_read(self):
        try:
            from pypdf import PdfWriter
        except ImportError:
            self.skipTest("optional pypdf is not installed")
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        with (self.inputs / "scan.pdf").open("wb") as stream:
            writer.write(stream)
        corpus = prepare(self.inputs, self.root / "scan", ["scan.pdf"])
        self.assertEqual(corpus["evidence"], [])
        self.assertIn("no_text", corpus["sources"][0]["warnings"][0])
        with self.assertRaisesRegex(ValueError, "没有可用"):
            generate(self.root / "scan", self.root / "scan-run", self.prompt, "extractive", {})


if __name__ == "__main__":
    unittest.main()
