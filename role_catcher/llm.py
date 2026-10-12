"""OpenRouter client. The `openai` package is used only as an OpenAI-compatible
HTTP client pointed at OpenRouter; there is no anthropic dependency."""
from __future__ import annotations
import json
import os
import re
from dataclasses import dataclass, field

from openai import OpenAI

from . import spend
from .schema import validate


class LLMError(Exception):
    pass


@dataclass
class LLMResult:
    text: str
    data: dict | None = None            # parsed + validated JSON when a schema was given
    citations: list[dict] = field(default_factory=list)  # [{url, title}] from web search annotations
    cost: float = 0.0
    model_used: str = ""


def get_client(config: dict) -> OpenAI:
    cfg = config["openrouter"]
    if not os.environ.get("OPENROUTER_API_KEY"):
        raise LLMError("OPENROUTER_API_KEY is not set. For local runs put OPENROUTER_API_KEY=... "
                       "in a file named .env in the project folder (it is gitignored).")
    return OpenAI(
        base_url=cfg["base_url"],
        api_key=os.environ["OPENROUTER_API_KEY"],  # sent as a Bearer token
        default_headers={"HTTP-Referer": cfg["app_url"], "X-Title": cfg["app_title"]},
    )


def _system_content(cached_text: str, ttl: str) -> list[dict]:
    """Stable material (profile, calibration summaries) goes first with a cache
    breakpoint. Anthropic models need explicit cache_control; other providers
    cache automatically and ignore it."""
    cache = {"type": "ephemeral"}
    if ttl == "1h":
        cache["ttl"] = "1h"
    return [{"type": "text", "text": cached_text, "cache_control": cache}]


def _extract_citations(message: dict) -> list[dict]:
    seen, out = set(), []
    for ann in message.get("annotations") or []:
        if ann.get("type") != "url_citation":
            continue
        c = ann.get("url_citation") or {}
        url = c.get("url")
        if url and url not in seen:
            seen.add(url)
            out.append({"url": url, "title": c.get("title", "")})
    return out


def _parse_json(text: str):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise


def _log(resp: dict, purpose: str, requested: str, attempt: int, web: bool) -> float:
    usage = resp.get("usage") or {}
    details = usage.get("prompt_tokens_details") or {}
    cost = usage.get("cost")
    spend.log_call({
        "purpose": purpose,
        "model_requested": requested,
        "model_used": resp.get("model"),
        "attempt": attempt,
        "web_search": web,
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "cached_tokens": details.get("cached_tokens"),
        "cache_write_tokens": details.get("cache_write_tokens"),
        "cost": cost if cost is not None else 0.0,
        "cost_missing": cost is None,
    })
    return float(cost or 0.0)


def complete(
    client: OpenAI,
    config: dict,
    role: str,                 # "scoring" or "vetting": picks model + fallback from config
    system_cached: str,        # stable text: cached by the provider
    user: str,                 # per-call text (the job, the company)
    schema: dict | None = None,
    schema_name: str = "result",
    web_search: bool = False,
    purpose: str | None = None,
) -> LLMResult:
    cfg = config["openrouter"]
    primary, fallback = cfg[f"{role}_model"], cfg.get(f"{role}_fallback")
    purpose = purpose or role

    extra_body: dict = {
        "models": [m for m in (primary, fallback) if m],   # fallback list
        "provider": {
            "data_collection": cfg["privacy"]["data_collection"],
            "require_parameters": bool(schema),            # only endpoints that honor json_schema
        },
    }
    if cfg["privacy"].get("zdr"):
        extra_body["provider"]["zdr"] = True
    if web_search:
        plugin = {"id": "web", "max_results": cfg["web_search"]["max_results"]}
        if cfg["web_search"].get("engine"):
            plugin["engine"] = cfg["web_search"]["engine"]
        extra_body["plugins"] = [plugin]

    messages = [
        {"role": "system", "content": _system_content(system_cached, cfg["prompt_cache_ttl"])},
        {"role": "user", "content": user},
    ]
    kwargs: dict = {}
    if schema:
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": schema_name, "strict": True, "schema": schema},
        }
        messages[0]["content"][0]["text"] += (
            "\n\nReturn JSON only, matching the required schema. No prose outside the JSON."
        )

    total_cost, last_error = 0.0, ""
    for attempt in (1, 2):     # one retry, only for invalid JSON
        resp = client.chat.completions.create(
            model=primary, messages=messages, extra_body=extra_body, **kwargs
        ).model_dump()
        total_cost += _log(resp, purpose, primary, attempt, web_search)
        message = (resp.get("choices") or [{}])[0].get("message") or {}
        text = message.get("content") or ""
        citations = _extract_citations(message)
        result = LLMResult(text=text, citations=citations, cost=total_cost,
                           model_used=resp.get("model") or primary)
        if not schema:
            return result
        try:
            data = _parse_json(text)
            errors = validate(data, schema)
        except (json.JSONDecodeError, ValueError) as e:
            data, errors = None, [f"not valid JSON: {e}"]
        if not errors:
            result.data = data
            return result
        last_error = "; ".join(errors[:5])
        messages = messages + [
            {"role": "assistant", "content": text},
            {"role": "user", "content": f"Your JSON failed validation: {last_error}. "
                                        "Return corrected JSON only."},
        ]
    raise LLMError(f"{purpose}: model output failed schema validation twice ({last_error})")
