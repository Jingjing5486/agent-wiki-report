"""A real DeepSeek JSON API adapter and an explicitly non-LLM offline adapter."""

import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class ProviderError(ValueError):
    pass


def load_env(path: Path) -> None:
    """Load one local secret without shell execution or variable interpolation."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or key.strip() != "DEEPSEEK_API_KEY":
            raise ValueError(".env 只支持 DEEPSEEK_API_KEY=...；模型参数放在 TOML 配置中")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault("DEEPSEEK_API_KEY", value)


def check_deepseek(config: dict) -> None:
    if not os.environ.get("DEEPSEEK_API_KEY", "").strip():
        raise ProviderError("缺少 DEEPSEEK_API_KEY，请填写项目 .env 或设置环境变量")
    url = urllib.parse.urlsplit(config["base_url"])
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ProviderError("base_url 必须为无账号、查询参数或片段的 HTTPS 地址")
    if config.get("thinking") not in {"enabled", "disabled"}:
        raise ProviderError("thinking 必须为 enabled 或 disabled")
    if not isinstance(config.get("model"), str) or not config["model"].strip():
        raise ProviderError("请配置 model")
    for key in ("max_tokens", "timeout_seconds", "max_input_chars"):
        if not isinstance(config.get(key), int) or config[key] <= 0:
            raise ProviderError(f"{key} 必须为正整数")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ProviderError("API 返回重定向，请核查 base_url；未转发密钥")


def deepseek(system: str, user: str, config: dict) -> tuple[str, dict]:
    check_deepseek(config)
    payload = {"model": config["model"],
               "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
               "response_format": {"type": "json_object"}, "stream": False,
               "max_tokens": config["max_tokens"], "thinking": {"type": config["thinking"]}}
    if config["thinking"] == "disabled":
        payload["temperature"] = config.get("temperature", 0)
    request = urllib.request.Request(
        config["base_url"].rstrip("/") + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + os.environ["DEEPSEEK_API_KEY"].strip()})
    try:
        import certifi
        context = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        context = ssl.create_default_context()
    try:
        # No automatic retry: a repeated request may incur a second charge.
        with urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=context)).open(
                request, timeout=config["timeout_seconds"]) as response:
            body = json.load(response)
    except urllib.error.HTTPError as exc:
        raise ProviderError(f"DeepSeek HTTP {exc.code}；检查密钥、配额或模型参数。未自动重试。") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = type(getattr(exc, "reason", exc)).__name__
        raise ProviderError(f"DeepSeek 网络连接失败或超时（{reason}）；未自动重试。") from None
    except (json.JSONDecodeError, UnicodeError):
        raise ProviderError("DeepSeek 返回了无法解析的响应") from None
    try:
        choice = body["choices"][0]
        if choice["finish_reason"] != "stop":
            raise ProviderError("模型未正常完成（可能超过输出预算）；未发布 Wiki。")
        content = choice["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise ProviderError("模型返回空 JSON 内容；请检查 Prompt 或重新运行。")
        return content, {"model": body.get("model"), "usage": body.get("usage", {}),
                         "finish_reason": choice["finish_reason"]}
    except (KeyError, IndexError, TypeError):
        raise ProviderError("DeepSeek 响应缺少预期字段") from None


def extractive(corpus: dict) -> tuple[str, dict]:
    """Copy input chunks, not a semantic generator and not a prompt experiment."""
    pages = []
    for source in corpus["sources"]:
        evidence = [e for e in corpus["evidence"] if e["source_id"] == source["source_id"]]
        if evidence:
            pages.append({"slug": source["source_id"], "title": Path(source["path"]).stem,
                          "sections": [{"heading": "原文摘录（离线演示）", "claims": [
                              {"text": e["text"], "kind": "fact", "evidence_ids": [e["evidence_id"]]}
                              for e in evidence]}]})
    return json.dumps({"pages": pages}, ensure_ascii=False), {
        "model": None, "usage": {}, "note": "extractive：原文摘录，无 LLM 调用，不体现 Prompt 差异"}
