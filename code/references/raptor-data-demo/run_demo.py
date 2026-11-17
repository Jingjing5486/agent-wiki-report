"""Build a small RAPTOR tree from the prepared page JSONL corpus.

The runner uses the upstream RAPTOR chunking, SBERT embeddings, UMAP/GMM
clustering, and recursive tree builder. It only writes demo outputs locally.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from openai import OpenAI

from raptor.EmbeddingModels import BaseEmbeddingModel, SBertEmbeddingModel
from raptor.SummarizationModels import BaseSummarizationModel
from raptor.cluster_tree_builder import ClusterTreeBuilder, ClusterTreeConfig
from raptor.tree_structures import Node, Tree
from raptor.utils import split_text


DEFAULT_INPUT = Path("data-prepared/demo-v2/pages.jsonl")
DEFAULT_MODEL = "sentence-transformers/multi-qa-mpnet-base-cos-v1"
DEFAULT_LLM = "deepseek-v4-pro"
LOCAL_QWEN_REPO = "models--Qwen--Qwen2.5-1.5B-Instruct"


def read_pages(
    path: Path,
    max_pages: int | None,
    global_scope: bool,
    source_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Read either a one-source sample or all pages for a cross-source tree."""
    pages = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            if not str(row.get("search_text", "")).strip():
                continue
            if source_ids and row.get("source_id") not in source_ids:
                continue
            if not global_scope and pages and row["source_id"] != pages[0]["source_id"]:
                continue
            pages.append(row)
            if max_pages is not None and len(pages) >= max_pages:
                break
    if not pages:
        raise ValueError(f"No usable search_text pages found in {path}")
    return pages


def prepare_leaves(pages: list[dict[str, Any]], tokenizer: Any) -> tuple[list[str], list[dict[str, Any]]]:
    """Split page text at sentence boundaries and retain stable page locators."""
    texts, provenance = [], []
    for page in pages:
        chunks = split_text(page["search_text"], tokenizer=tokenizer, max_tokens=100)
        for chunk_index, chunk in enumerate(chunks):
            if not chunk.strip():
                continue
            texts.append(chunk)
            provenance.append({
                "source_id": page["source_id"],
                "source_name": page["source_name"],
                "page": page["page"],
                "chunk_in_page": chunk_index,
            })
    if len(texts) < 12:
        raise ValueError(f"Need at least 12 leaf chunks for RAPTOR clustering; got {len(texts)}")
    return texts, provenance


def load_api_key(path: Path | None) -> str:
    """Read the DeepSeek key without printing or copying it into the output tree."""
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key
    if path and path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            name, separator, value = line.strip().partition("=")
            if separator and name.strip() == "DEEPSEEK_API_KEY":
                return value.strip().strip("\"'")
    raise ValueError("Set DEEPSEEK_API_KEY or pass --env-file with that variable")


class DeepSeekPaperSummarizer(BaseSummarizationModel):
    """Call a DeepSeek OpenAI-compatible endpoint with the paper's summary prompt."""

    def __init__(self, api_key: str, model: str, base_url: str, cache_dir: Path) -> None:
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=180, max_retries=0)
        self.model = model
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.calls = 0
        self.cache_hits = 0
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def summarize(self, context: str, max_tokens: int = 100) -> str:
        cache_key = hashlib.sha256(
            f"{self.model}\n{max_tokens}\n{context}".encode("utf-8")
        ).hexdigest()
        cache_file = self.cache_dir / f"{cache_key}.json"
        if cache_file.exists():
            self.cache_hits += 1
            return json.loads(cache_file.read_text(encoding="utf-8"))["summary"]
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "You are a Summarizing Text Portal"},
                {"role": "user", "content": "Write a summary of the following, including as many key details as possible: " + context + ":"},
            ],
            max_tokens=max_tokens,
            temperature=0,
            extra_body={"thinking": {"type": "disabled"}},
        )
        self.calls += 1
        if response.usage:
            for field in self.usage:
                self.usage[field] += getattr(response.usage, field, 0) or 0
        summary = response.choices[0].message.content
        if not summary or not summary.strip():
            raise ValueError("Summarization endpoint returned an empty summary")
        summary = summary.strip()
        cache_file.write_text(json.dumps({"summary": summary}, ensure_ascii=False), encoding="utf-8")
        return summary


class CachedQwenEmbeddingModel(BaseEmbeddingModel):
    """Use a locally cached Chinese-capable model when the SBERT download is unavailable."""

    def __init__(self, model_path: Path) -> None:
        import torch
        from transformers import AutoModel, AutoTokenizer

        self.torch = torch
        self.device = "mps" if torch.backends.mps.is_available() else "cpu"
        self.tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
        self.model = AutoModel.from_pretrained(model_path, local_files_only=True).to(self.device)
        self.model.eval()

    def create_embeddings(self, texts: list[str], batch_size: int = 8) -> list[Any]:
        """Mean-pool token vectors in batches to avoid repeated model setup overhead."""
        vectors = []
        for start in range(0, len(texts), batch_size):
            batch = self.tokenizer(texts[start:start + batch_size], padding=True,
                                   truncation=True, max_length=512, return_tensors="pt")
            batch = {key: value.to(self.device) for key, value in batch.items()}
            with self.torch.inference_mode():
                hidden = self.model(**batch).last_hidden_state
                mask = batch["attention_mask"].unsqueeze(-1)
                pooled = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1)
            vectors.extend(pooled.cpu().numpy())
        return vectors

    def create_embedding(self, text: str) -> Any:
        return self.create_embeddings([text])[0]


def find_cached_qwen() -> Path:
    """Find the Qwen snapshot already cached on this workstation."""
    snapshots = Path.home() / ".cache" / "huggingface" / "hub" / LOCAL_QWEN_REPO / "snapshots"
    candidates = sorted(path for path in snapshots.glob("*") if (path / "config.json").is_file())
    if not candidates:
        raise FileNotFoundError(f"No local snapshot found for {LOCAL_QWEN_REPO}")
    return candidates[-1]


def descendants(node_id: int, nodes: dict[int, Any], leaf_count: int) -> list[int]:
    """Return the leaf-node IDs covered by a node."""
    node = nodes[node_id]
    if node_id < leaf_count:
        return [node_id]
    result = []
    for child_id in sorted(node.children):
        result.extend(descendants(child_id, nodes, leaf_count))
    return result


def serialize_tree(tree: Tree, provenance: list[dict[str, Any]]) -> dict[str, Any]:
    """Serialize text, hierarchy, and source/page provenance without embeddings."""
    leaf_count = len(tree.leaf_nodes)
    serialized = []
    for layer, layer_nodes in sorted(tree.layer_to_nodes.items()):
        for node in sorted(layer_nodes, key=lambda item: item.index):
            leaf_ids = descendants(node.index, tree.all_nodes, leaf_count)
            locators = sorted({
                (provenance[i]["source_id"], provenance[i]["source_name"], provenance[i]["page"])
                for i in leaf_ids
            })
            serialized.append({
                "node_id": node.index,
                "layer": layer,
                "text": node.text,
                "children": sorted(node.children),
                "leaf_chunk_count": len(leaf_ids),
                "sources": [{"source_id": sid, "source_name": name, "pages": sorted({p for s, n, p in locators if (s, n) == (sid, name)})}
                            for sid, name in sorted({(s, n) for s, n, _ in locators})],
            })
    return {"num_layers": tree.num_layers, "leaf_node_count": leaf_count, "nodes": serialized}


def run(args: argparse.Namespace) -> None:
    """Construct the tree, call the LLM for parent nodes, and write run metadata."""
    input_path = args.input.resolve()
    global_scope = args.scope == "global"
    pages = read_pages(input_path, None if global_scope else args.max_pages,
                       global_scope, set(args.source_id) if args.source_id else None)
    tokenizer = __import__("tiktoken").get_encoding("cl100k_base")
    leaf_texts, provenance = prepare_leaves(pages, tokenizer)
    output_dir = args.output or Path(__file__).parent / "artifacts" / args.scope
    summarizer = DeepSeekPaperSummarizer(load_api_key(args.env_file), args.llm_model,
                                          args.base_url, output_dir / "summary-cache")
    if args.embedding_backend == "sbert":
        embedder = SBertEmbeddingModel(DEFAULT_MODEL)
        embedding_name = "SBERT"
        leaf_embeddings = embedder.model.encode(leaf_texts, batch_size=32,
                                                show_progress_bar=False)
    else:
        model_path = args.local_qwen_path or find_cached_qwen()
        embedder = CachedQwenEmbeddingModel(model_path)
        embedding_name = "Qwen2.5-1.5B-Instruct mean pooling (local)"
        leaf_embeddings = embedder.create_embeddings(leaf_texts)
    config = ClusterTreeConfig(
        tokenizer=tokenizer,
        max_tokens=100,
        num_layers=args.max_layers,
        summarization_length=100,
        summarization_model=summarizer,
        embedding_models={embedding_name: embedder},
        cluster_embedding_model=embedding_name,
        reduction_dimension=10,
        clustering_params={"threshold": 0.1},
    )
    builder = ClusterTreeBuilder(config)
    started = time.monotonic()
    leaf_nodes = {
        index: Node(text, index, set(), {embedding_name: leaf_embeddings[index]})
        for index, text in enumerate(leaf_texts)
    }
    layers = {0: list(leaf_nodes.values())}
    all_nodes = dict(leaf_nodes)
    roots = builder.construct_tree(all_nodes, all_nodes, layers, use_multithreading=False)
    tree = Tree(all_nodes, roots, leaf_nodes, builder.num_layers, layers)

    payload = serialize_tree(tree, provenance)
    payload["run"] = {
        "scope": args.scope,
        "input": str(input_path),
        "source_count": len({row["source_id"] for row in pages}),
        "page_count": len(pages),
        "leaf_node_count": len(leaf_texts),
        "llm_model": args.llm_model,
        "embedding_model": DEFAULT_MODEL if args.embedding_backend == "sbert" else embedding_name,
        "llm_summary_calls": summarizer.calls,
        "llm_cache_hits": summarizer.cache_hits,
        "llm_usage": summarizer.usage,
        "elapsed_seconds": round(time.monotonic() - started, 2),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / "raptor-tree.json"
    output_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output_file), **payload["run"], "tree_layers": payload["num_layers"]}, ensure_ascii=False, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a RAPTOR demo tree from prepared pages")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--scope", choices=("sample", "global"), default="sample",
                        help="sample: first pages from one source; global: every nonempty page across all sources")
    parser.add_argument("--source-id", action="append", default=[],
                        help="limit global scope to this source_id; may be repeated")
    parser.add_argument("--max-pages", type=int, default=3)
    parser.add_argument("--max-layers", type=int, default=3)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--llm-model", default=DEFAULT_LLM)
    parser.add_argument("--base-url", default="https://api.deepseek.com")
    parser.add_argument("--embedding-backend", choices=("sbert", "local-qwen"), default="sbert")
    parser.add_argument("--local-qwen-path", type=Path)
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
