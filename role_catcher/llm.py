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


class CapReached(LLMError):
    """A daily cap from config.yaml `limits` stops further model calls today."""


def check_caps(config: dict, purpose: str) -> None:
    """Raise CapReached if today's real spend or call count (from logs/spend.jsonl)
    has hit a cap. Called before every model call, so no step can overspend."""
    lim, tz = config.get("limits", {}), config["timezone"]
    max_total = lim.get("max_spend_per_day_usd")
    if max_total is not None and spend.spend_today(tz) >= max_total:
        raise CapReached(f"daily OpenRouter spend cap of ${max_total:.2f}")
    max_calls = lim.get(f"max_{purpose}_calls_per_day")
    if max_calls is not None and spend.calls_today(tz, purpose) >= max_calls:
        raise CapReached(f"daily {purpose} cap of {max_calls} calls")
    max_purpose = lim.get(f"max_{purpose}_spend_per_day_usd")
    if max_purpose is not None and spend.spend_today(tz, purpose) >= max_purpose:
        raise CapReached(f"daily {purpose} spend cap of ${max_purpose:.2f}")


@dataclass
class LLMResult:
    text: str
    data: dict | None = None            # parsed + validated JSON when a schema was given
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
        max_retries=4,      # the client backs off and honors Retry-After on 429/5xx/network errors
        timeout=120,
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


def _log(resp: dict, purpose: str, requested: str, attempt: int) -> float:
    usage = resp.get("usage") or {}
    details = usage.get("prompt_tokens_details") or {}
    cost = usage.get("cost")
    spend.log_call({
        "purpose": purpose,
        "model_requested": requested,
        "model_used": resp.get("model"),
        "attempt": attempt,
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
    role: str,                 # "scoring": picks model + fallback from config
    system_cached: str,        # stable text: cached by the provider
    user: str,                 # per-call text (the job, the company)
    schema: dict | None = None,
    schema_name: str = "result",
    purpose: str | None = None,
) -> LLMResult:
    cfg = config["openrouter"]
    primary, fallback = cfg[f"{role}_model"], cfg.get(f"{role}_fallback")
    purpose = purpose or role
    check_caps(config, purpose)

    extra_body: dict = {
        "models": [m for m in (primary, fallback) if m],   # fallback list
        "provider": {
            "data_collection": cfg["privacy"]["data_collection"],
            "require_parameters": bool(schema),            # only endpoints that honor json_schema
        },
    }
    if cfg["privacy"].get("zdr"):
        extra_body["provider"]["zdr"] = True

    messages = [
        {"role": "system", "content": _system_content(system_cached, cfg["prompt_cache_ttl"])},
        {"role": "user", "content": user},
    ]
    # OpenRouter reserves credit for the full max_tokens up front (the model's
    # 65k default would reserve ~$3), so cap it near the real reply size.
    kwargs: dict = {"max_tokens": cfg.get("max_tokens", 4096)}
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
        total_cost += _log(resp, purpose, primary, attempt)
        message = (resp.get("choices") or [{}])[0].get("message") or {}
        text = message.get("content") or ""
        result = LLMResult(text=text, cost=total_cost,
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
