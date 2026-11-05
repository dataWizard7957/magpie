"""
Magpie: one place to configure model providers.

Switch the session agent between providers with one env var:
    MAGPIE_PROVIDER=meta      -> Muse Spark on Meta Model API (development)
    MAGPIE_PROVIDER=glimmer   -> Muse Glimmer on Modal (final version / Open Weight bonus)

Keys live in a .env file (never committed). See .env.example.
All model calls in Magpie go through this file.
"""
from __future__ import annotations

import json
import os
import re

from openai import BadRequestError, OpenAI

# Load .env if python-dotenv is installed
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


PROVIDERS = {
    # Muse Spark on Meta Model API
    "meta": {
        "base_url": os.getenv("META_BASE_URL", "https://api.meta.ai/v1"),
        "api_key": os.getenv("MODEL_API_KEY"),
        "model": os.getenv("META_MODEL", "muse-spark-1.3"),
        "headers": {},
    },
    # Muse Glimmer (open weights) served by Ollama on Modal
    "glimmer": {
        "base_url": os.getenv("GLIMMER_URL"),  # https://<your-app>.modal.run/v1
        "api_key": "unused",                   # Ollama ignores it; proxy auth is in headers
        "model": os.getenv("GLIMMER_MODEL", "muse-glimmer:30b-q4_K_M"),
        "headers": {
            "Modal-Key": os.getenv("MODAL_PROXY_KEY", ""),
            "Modal-Secret": os.getenv("MODAL_PROXY_SECRET", ""),
        },
    },
}

# Meta-only models (not switchable)
MUSE_IMAGE_MODEL = os.getenv("MUSE_IMAGE_MODEL", "muse-image-1.0")
MUSE_TRANSCRIBE_MODEL = os.getenv("MUSE_TRANSCRIBE_MODEL", "muse-voice-transcribe-1.0")
META_ASR_URL = os.getenv("META_ASR_URL", "https://api.meta.ai/v1/asr/transcribe")

# Reasoning models spend tokens thinking before answering, so keep this generous
DEFAULT_MAX_TOKENS = int(os.getenv("MAGPIE_MAX_TOKENS", "1024"))
DEFAULT_REASONING = os.getenv("MAGPIE_REASONING", "low")  # low | medium | high | xhigh


def active_provider() -> str:
    """Provider used by the session agent and grounding check."""
    return os.getenv("MAGPIE_PROVIDER", "meta")


ACTIVE = active_provider()
_clients: dict[str, tuple[OpenAI, str]] = {}


def get_client(name: str | None = None) -> tuple[OpenAI, str]:
    """Return (client, model_id) for a provider. Fails loudly if config is missing."""
    name = name or active_provider()
    if name in _clients:
        return _clients[name]
    if name not in PROVIDERS:
        raise ValueError(f"Unknown provider '{name}'. Use one of: {', '.join(PROVIDERS)}")
    p = PROVIDERS[name]

    missing = [k for k in ("base_url", "api_key") if not p[k]]
    if name == "glimmer" and not (p["headers"]["Modal-Key"] and p["headers"]["Modal-Secret"]):
        missing.append("MODAL_PROXY_KEY / MODAL_PROXY_SECRET")
    if missing:
        raise RuntimeError(f"Provider '{name}' is missing: {', '.join(missing)}. Check your .env")

    client = OpenAI(
        base_url=p["base_url"],
        api_key=p["api_key"],
        default_headers=p["headers"],
        timeout=300,  # first Glimmer call loads ~18 GB onto the GPU
    )
    _clients[name] = (client, p["model"])
    return _clients[name]


def meta_api_key() -> str:
    key = PROVIDERS["meta"]["api_key"]
    if not key:
        raise RuntimeError("MODEL_API_KEY is not set. Check your .env")
    return key


def chat(messages: list[dict], provider: str | None = None, max_tokens: int = DEFAULT_MAX_TOKENS,
         reasoning: str | None = DEFAULT_REASONING, **kwargs) -> str:
    """Send a chat completion and return the answer text (never the reasoning)."""
    provider = provider or active_provider()
    client, model = get_client(provider)
    extra = {"reasoning_effort": reasoning} if reasoning else None
    try:
        resp = client.chat.completions.create(
            model=model, messages=messages, max_tokens=max_tokens, extra_body=extra, **kwargs)
    except BadRequestError as e:
        # Some servers reject reasoning_effort; retry once without it
        if extra and "reason" in str(e).lower():
            resp = client.chat.completions.create(
                model=model, messages=messages, max_tokens=max_tokens, **kwargs)
        else:
            raise
    text = (resp.choices[0].message.content or "").strip()
    if not text:
        raise RuntimeError(
            f"Empty reply from {provider} (finish_reason={resp.choices[0].finish_reason}). "
            "Raise max_tokens or lower reasoning.")
    return text


def parse_json(text: str):
    """Pull the first JSON object or array out of a model reply."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if not starts:
        raise ValueError("No JSON found in reply")
    obj, _ = json.JSONDecoder().raw_decode(text[min(starts):])
    return obj


def chat_json(messages: list[dict], provider: str | None = None, retries: int = 1, **kwargs):
    """Chat and parse JSON from the reply. Retries once on bad JSON or an empty reply."""
    last_err: Exception | None = None
    msgs = list(messages)
    for _ in range(retries + 1):
        try:
            text = chat(msgs, provider=provider, **kwargs)
        except RuntimeError as e:   # empty reply: try again
            last_err = e
            continue
        try:
            return parse_json(text)
        except (ValueError, json.JSONDecodeError) as e:
            last_err = e
            msgs = msgs + [{"role": "assistant", "content": text},
                           {"role": "user", "content": "That was not valid JSON. Reply with only the JSON."}]
    raise RuntimeError(f"Model did not return valid JSON: {last_err}")