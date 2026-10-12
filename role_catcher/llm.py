"""OpenRouter client. Uses the openai package only as an OpenAI-compatible client."""
import os
from openai import OpenAI


def get_client(base_url: str) -> OpenAI:
    return OpenAI(base_url=base_url, api_key=os.environ["OPENROUTER_API_KEY"])


def complete(client: OpenAI, model: str, system: str, user: str) -> str:
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    return resp.choices[0].message.content or ""
