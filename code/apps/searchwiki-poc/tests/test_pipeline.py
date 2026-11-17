import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from searchwiki.corpus import load_corpus
from searchwiki.files import read_json, write_json
from searchwiki.llm import Client, DEFAULTS, validate_config
from searchwiki.offline import respond
from searchwiki.pipeline import build
from searchwiki.planning import include_cited_boundary_pages, make_tree, make_units, owner, validate_toc
from searchwiki.registry import Registry
from searchwiki.schema import validate_joint, validate_synthesis
from searchwiki.validation import validate_build


def fixture(root):
    root.mkdir()
    sources, pages = [], []
    for number in [1, 2]:
        sid = f"src-test{number}"
        source = {"source_id": sid, "source_name": f"0{number}_公司报告.pdf", "source_sha256": "a" * 64,
                  "page_count": 3, "outline": [{"title": "概览", "page": 1, "level": 1},
                                               {"title": "业务", "page": 2, "level": 1}]}
        sources.append(source)
        for p in range(1, 4):
            pages.append({"source_id": sid, "source_name": source["source_name"], "source_sha256": "a" * 64,
                          "page": p, "search_text": f"公司{number}第{p}页的真实来源文本。[文本不是链接](不存在.md)",
                          "quality_flags": ["ocr_needs_visual_review"] if p == 3 else [],
                          "blocks": [], "heading_candidates": [], "ocr": {"status": "ok"}})
    for name, rows in [("sources", sources), ("pages", pages)]:
        (root / f"{name}.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows))
    return root


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = fixture(self.root / "data")
        self.output = self.root / "run"
        self.config = {**DEFAULTS, "unit_max_pages": 2}

    def build(self, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return build(self.data, self.output, "offline", self.config, **kwargs)

    def test_full_build_links_sources_and_resume(self):
        run = self.build()
        self.assertEqual(run["status"], "complete")
        self.assertEqual(run["metrics"]["source_pages"], 6)
        self.assertEqual(run["metrics"]["cross_document_topics"], 1)
        self.assertTrue(validate_build(self.output)["structural_pass"])
        requests = list((self.output / "requests").glob("*.json"))
        again = self.build(resume=True)
        self.assertEqual(len(list((self.output / "requests").glob("*.json"))), len(requests))
        self.assertGreater(again["usage"]["cache_hits_this_invocation"], 0)
        self.assertEqual(read_json(self.output / "wiki/sources/src-test1.json")[2]["quality_flags"],
                         ["ocr_needs_visual_review"])

    def test_no_overwrite_and_changed_input_resume(self):
        self.build()
        with self.assertRaisesRegex(ValueError, "已存在"):
            self.build()
        with (self.data / "pages.jsonl").open("a") as f:
            f.write("\n")
        with self.assertRaisesRegex(ValueError, "已改变"):
            self.build(resume=True)

    def test_missing_and_duplicate_pages_rejected(self):
        path = self.data / "pages.jsonl"
        lines = path.read_text().splitlines()
        path.write_text("\n".join(lines[:-1]))
        with self.assertRaisesRegex(ValueError, "缺失或重复"):
            load_corpus(self.data)
        path.write_text("\n".join(lines + [lines[0]]))
        with self.assertRaisesRegex(ValueError, "缺失或重复"):
            load_corpus(self.data)

    def test_snapshot_and_link_tampering_detected(self):
        self.build()
        path = self.output / "wiki/sources/src-test1.json"
        rows = read_json(path)
        rows[0]["search_text"] = "tampered"
        write_json(path, rows)
        with (self.output / "wiki/index.md").open("a") as f:
            f.write("\n[bad](missing.md)\n")
        errors = validate_build(self.output)["errors"]
        self.assertTrue(any("快照被修改" in x for x in errors))
        self.assertTrue(any("失效" in x for x in errors))

    def test_failed_run_resumes_from_validated_responses(self):
        from unittest.mock import patch
        original = Client.ask
        counter = [0]
        def interrupt(client, *args, **kwargs):
            counter[0] += 1
            if counter[0] == 3:
                raise ValueError("simulated failure")
            return original(client, *args, **kwargs)
        with patch.object(Client, "ask", interrupt), self.assertRaisesRegex(ValueError, "simulated"):
            self.build()
        self.assertEqual(read_json(self.output / "run.json")["status"], "failed")
        self.assertFalse((self.output / ".build.lock").exists())
        result = self.build(resume=True)
        self.assertEqual(result["status"], "complete")
        self.assertNotIn("error_type", result)
        self.assertGreaterEqual(result["usage"]["cache_hits_this_invocation"], 2)


class StructureTests(unittest.TestCase):
    def test_nested_ranges_and_ownership(self):
        tree = make_tree({"sections": [
            {"title": "A", "level": 1, "start_page": 2},
            {"title": "A1", "level": 2, "start_page": 3},
            {"title": "A2", "level": 2, "start_page": 5},
            {"title": "B", "level": 1, "start_page": 7}]}, 8)
        self.assertEqual(tree[0]["title"], "前置资料")
        self.assertEqual((tree[1]["start_page"], tree[1]["end_page"]), (2, 6))
        self.assertEqual(owner(tree, 4), tree[2]["id"])
        self.assertTrue(tree[2]["path"].startswith(tree[1]["path"] + "/"))

    def test_toc_rejects_skips_and_out_of_range(self):
        for entries in [
            [{"title": "bad", "level": 2, "start_page": 1}],
            [{"title": "bad", "level": 1, "start_page": 9}]]:
            with self.assertRaises(ValueError):
                validate_toc({"sections": entries}, 8)

    def test_same_page_sibling_titles_are_combined_without_empty_ranges(self):
        tree = make_tree({"sections": [
            {"title": "公司案例", "level": 1, "start_page": 1},
            {"title": "Salesforce", "level": 2, "start_page": 2},
            {"title": "ServiceNow", "level": 2, "start_page": 2},
            {"title": "下一节", "level": 1, "start_page": 3}]}, 4)
        self.assertEqual(tree[1]["title"], "Salesforce / ServiceNow")
        self.assertEqual((tree[1]["start_page"], tree[1]["end_page"]), (2, 2))
        self.assertEqual(owner(tree, 2), tree[1]["id"])

    def test_full_text_not_truncated_when_units_split(self):
        tree = make_tree({"sections": [{"title": "A", "level": 1, "start_page": 1}]}, 3)
        doc = {"pages": [{"page": p, "search_text": "文" * 20} for p in range(1, 4)]}
        units = make_units(doc, tree, 35, 2)
        self.assertEqual(len(units), 3)
        self.assertEqual(sum(len(p["text"]) for u in units for p in u["pages"]), 60)
        with self.assertRaisesRegex(ValueError, "没有截断"):
            make_units(doc, tree, 10, 2)

    def test_registry_alias_reuse_and_conflict(self):
        r = Registry()
        def action(title, aliases=(), existing=None):
            return {"title": title, "aliases": list(aliases), "existing_topic_id": existing,
                    "paragraphs": [{"text": "source", "pages": [1]}]}
        tid = r.register(action("股份回购", ["股票回购"]), "src-a", "u1")
        self.assertEqual(r.register(action("股票回购"), "src-b", "u1"), tid)
        r.register(action("人工智能"), "src-a", "u2")
        with self.assertRaisesRegex(ValueError, "多个"):
            r.register(action("股份回购", ["人工智能"]), "src-c", "u1")
        self.assertEqual(len(r.topics[tid]["contributions"]), 2)

    def test_joint_cannot_cite_another_section_page(self):
        payload = {"pages": [{"section_id": "s1", "page": 1}, {"section_id": "s2", "page": 2}],
                   "registry": [], "max_topics_per_unit": 4}
        result = {"summaries": [
            {"section_id": "s1", "paragraphs": [{"text": "x", "pages": [2]}]},
            {"section_id": "s2", "paragraphs": [{"text": "x", "pages": [2]}]}], "topics": []}
        with self.assertRaisesRegex(ValueError, "页范围"):
            validate_joint(result, payload)

    def test_topic_must_preserve_contributing_sources(self):
        evidence = [{"text": sid, "citations": [{"source_id": sid, "page": 1}]} for sid in ["a", "b"]]
        with self.assertRaisesRegex(ValueError, "每个输入来源"):
            validate_synthesis({"paragraphs": evidence[:1], "related_topic_ids": []}, evidence, set(), "t1")

    def test_empty_topic_evidence_is_omitted_with_audit_note(self):
        payload = {"pages": [{"section_id": "s1", "page": 1}], "registry": [], "max_topics_per_unit": 4}
        result = {"summaries": [{"section_id": "s1", "paragraphs": [{"text": "valid", "pages": [1]}]}],
                  "topics": [{"title": "无资料主题", "existing_topic_id": None, "aliases": [],
                              "paragraphs": [{"text": "本单元未提供相关资料", "pages": []}]}]}
        validate_joint(result, payload)
        self.assertEqual(result["topics"], [])
        self.assertTrue(result["normalization_notes"])
        result["summaries"][0]["paragraphs"][0]["pages"] = []
        with self.assertRaises(ValueError):
            validate_joint(result, payload)

    def test_shared_boundary_page_keeps_real_citation_and_expands_range(self):
        tree = make_tree({"sections": [{"title": "前文", "level": 1, "start_page": 1},
                                      {"title": "后文", "level": 1, "start_page": 2}]}, 3)
        payload = {"pages": [{"section_id": "s001", "page": 1}, {"section_id": "s002", "page": 2},
                             {"section_id": "s002", "page": 3}],
                   "sections": tree, "registry": [], "max_topics_per_unit": 4}
        result = {"summaries": [{"section_id": "s001", "paragraphs": [{"text": "前文结尾", "pages": [2]}]},
                                {"section_id": "s002", "paragraphs": [{"text": "后文", "pages": [2]}]}], "topics": []}
        validate_joint(result, payload)
        include_cited_boundary_pages(tree, {"s001": [{"text": "前文结尾", "citations": [
            {"source_id": "a", "page": 2}]}]})
        self.assertEqual(tree[0]["end_page"], 2)
        self.assertEqual(tree[0]["planned_end_page"], 1)
        self.assertIn("range_note", tree[0])
        tree[0]["end_page"] = 1
        result["summaries"][0]["paragraphs"][0]["pages"] = [3]
        with self.assertRaises(ValueError):
            validate_joint(result, payload)


class ApiTests(unittest.TestCase):
    def test_json_repair_cache_and_usage(self):
        class Transport:
            def __init__(self):
                self.calls = 0
            def complete(self, messages):
                self.calls += 1
                return {"content": "bad JSON" if self.calls == 1 else '{"ok": true}',
                        "finish_reason": "stop", "usage": {"prompt_tokens": 2, "completion_tokens": 3,
                        "total_tokens": 5}, "model": "mock"}
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()):
            transport = Transport()
            client = Client(d, "deepseek", DEFAULTS, transport=transport)
            check = lambda r: self.assertTrue(r["ok"])
            client.ask("toc", {}, check, "mock")
            client.ask("toc", {}, check, "mock")
            self.assertEqual(transport.calls, 2)
            self.assertEqual(client.usage()["tokens"]["total_tokens"], 10)

    def test_truncated_output_not_cached(self):
        class Transport:
            def complete(self, messages):
                return {"content": '{"ok":true}', "finish_reason": "length", "usage": {}}
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()):
            client = Client(d, "deepseek", {**DEFAULTS, "repair_attempts": 0}, transport=Transport())
            with self.assertRaisesRegex(ValueError, "未通过"):
                client.ask("toc", {}, lambda r: r, "truncated")
            self.assertFalse(list((Path(d) / "cache").glob("*.json")))

    def test_credentials_not_allowed_in_config(self):
        with self.assertRaises(ValueError):
            validate_config({**DEFAULTS, "api_key": "must-not-be-logged"})
        with self.assertRaises(ValueError):
            validate_config({**DEFAULTS, "base_url": "https://user:secret@example.com"})


if __name__ == "__main__":
    unittest.main()
