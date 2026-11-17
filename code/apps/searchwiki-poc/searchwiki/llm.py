"""DeepSeek transport and validated, content-addressed checkpoints."""

import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .files import canonical, digest, now, read_json, write_json
from .offline import respond

PROMPTS = Path(__file__).parent / "prompts"
DEFAULTS = {"model": "deepseek-v4-pro", "base_url": "https://api.deepseek.com",
            "thinking": "disabled", "temperature": 0.0, "max_tokens": 12000,
            "timeout_seconds": 180, "max_input_chars": 160000, "unit_max_chars": 48000,
            "unit_max_pages": 32, "max_topics_per_unit": 4, "repair_attempts": 1}


def load_key(path=None):
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key
    path = Path(path) if path else Path(".env")
    if path.exists():
        for line in path.read_text().splitlines():
            name, sep, value = line.strip().partition("=")
            if sep and name.strip() == "DEEPSEEK_API_KEY":
                return value.strip().strip("\"'")
    raise ValueError("缺少DEEPSEEK_API_KEY；设置环境变量或通过--env-file指定本地.env")


def validate_config(config):
    unknown = set(config) - set(DEFAULTS)
    if unknown:
        raise ValueError("配置含未知字段；凭据只能通过环境变量或.env传入")
    for k in ("max_tokens", "timeout_seconds", "max_input_chars", "unit_max_chars",
              "unit_max_pages", "max_topics_per_unit"):
        if type(config[k]) is not int or config[k] <= 0:
            raise ValueError(f"{k}必须为正整数")
    if type(config["repair_attempts"]) is not int or not 0 <= config["repair_attempts"] <= 2:
        raise ValueError("repair_attempts应为0–2")
    if config["thinking"] not in {"enabled", "disabled"}:
        raise ValueError("thinking应为enabled/disabled")
    url = urllib.parse.urlsplit(config["base_url"])
    if (url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment):
        raise ValueError("base_url必须是无凭据、查询或片段的HTTPS地址")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("API重定向被拒绝，请检查base_url")


class DeepSeek:
    def __init__(self, config, key):
        self.config, self.key = config, key

    def complete(self, messages):
        c = self.config
        payload = {"model": c["model"], "messages": messages, "stream": False,
                   "response_format": {"type": "json_object"}, "max_tokens": c["max_tokens"],
                   "thinking": {"type": c["thinking"]}}
        if c["thinking"] == "disabled":
            payload["temperature"] = c["temperature"]
        request = urllib.request.Request(c["base_url"].rstrip("/") + "/chat/completions",
                  data=canonical(payload).encode(), headers={"Content-Type": "application/json",
                  "Authorization": "Bearer " + self.key})
        try:
            import certifi
            context = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            context = ssl.create_default_context()
        try:
            with urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=context)).open(
                    request, timeout=c["timeout_seconds"]) as response:
                body = json.load(response)
        except urllib.error.HTTPError as exc:
            raise ValueError(f"DeepSeek HTTP {exc.code}；未自动重试网络请求") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ValueError("DeepSeek网络连接失败或超时；检查网络后使用--resume") from None
        try:
            choice = body["choices"][0]
            return {"content": choice["message"].get("content") or "",
                    "finish_reason": choice["finish_reason"], "usage": body.get("usage", {}),
                    "model": body.get("model"), "response_id": body.get("id")}
        except (KeyError, IndexError, TypeError):
            raise ValueError("API响应缺少预期字段") from None


class Client:
    def __init__(self, output, provider, config, key=None, transport=None):
        self.output, self.provider, self.config = Path(output), provider, config
        self.transport = transport or (DeepSeek(config, key) if provider == "deepseek" else None)
        self.cache_hits = 0
        self.system = (PROMPTS / "system.md").read_text()

    def ask(self, task, payload, validate, label):
        prompt = (PROMPTS / f"{task}.md").read_text()
        messages = [{"role": "system", "content": self.system},
                    {"role": "user", "content": prompt + "\n输入JSON：\n" + canonical(payload)}]
        key = digest({"provider": self.provider, "config": self.config, "messages": messages})
        path = self.output / "cache" / f"{key}.json"
        if path.exists():
            value = read_json(path)["result"]
            validate(value)
            self.cache_hits += 1
            return value
        for attempt in range(self.config["repair_attempts"] + 1):
            if sum(len(m["content"]) for m in messages) > self.config["max_input_chars"]:
                raise ValueError("请求超过max_input_chars；调小编译单元或提高上限，不会静默截断")
            print(f"[{self.provider}] {label}" + (f" 修复{attempt}" if attempt else ""), flush=True)
            stamp = str(time.time_ns())
            request_path = self.output / "requests" / f"{stamp}.json"
            record = {"created_at": now(), "task": task, "label": label, "cache_key": key,
                      "attempt": attempt, "messages": messages, "provider": self.provider}
            write_json(request_path, record)
            start = time.monotonic()
            try:
                response = ({"content": canonical(respond(task, payload)), "finish_reason": "stop",
                             "usage": {}, "model": "offline-extractive"}
                            if self.provider == "offline" else self.transport.complete(messages))
            except Exception as exc:
                record.update(status="transport_failed", error_type=type(exc).__name__)
                write_json(request_path, record)
                raise
            record.update(response=response, elapsed_seconds=round(time.monotonic() - start, 3))
            try:
                if response["finish_reason"] != "stop":
                    raise ValueError("模型输出被截断或未正常结束")
                result = json.loads(response["content"])
                validate(result)
            except (ValueError, TypeError, KeyError) as exc:
                record.update(status="invalid", validation_error=str(exc)[:500])
                write_json(request_path, record)
                if attempt == self.config["repair_attempts"]:
                    raise ValueError(f"{label}未通过结构校验，详见requests/；可--resume重试") from None
                messages = messages[:2] + [
                    {"role": "assistant", "content": response["content"][:12000]},
                    {"role": "user", "content": "请重新返回完整JSON，修复以下结构错误：" + str(exc)[:500]}]
                continue
            record["status"] = "accepted"
            write_json(request_path, record)
            write_json(path, {"result": result, "request": request_path.name})
            return result

    def usage(self):
        total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        records = list((self.output / "requests").glob("*.json"))
        completed = failed = 0
        for path in records:
            r = read_json(path)
            if "response" in r:
                completed += 1
                for k in total:
                    total[k] += r["response"].get("usage", {}).get(k, 0)
            else:
                failed += 1
        return {"completed_responses": completed, "requests_without_response": failed,
                "cache_hits_this_invocation": self.cache_hits, "tokens": total}
