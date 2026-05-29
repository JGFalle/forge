"""Unified OSS LLM client — Groq, Google Gemini, or Ollama.

Priority order when auto-detecting:
  1. Groq (GROQ_API_KEY set)       — Llama 3.3 70B, fastest free option
  2. Gemini (GOOGLE_API_KEY set)   — Gemini 2.5 Flash, different architecture
  3. Ollama (running locally)      — zero cost, zero rate limits, offline

Call generate() and it routes to whatever is available.
All providers return a plain string.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from utils.config import get
from utils.logging import get_logger

load_dotenv(Path(".env"))
logger = get_logger(__name__)

# Default models per provider — overridable via config oss.*
_DEFAULTS = {
    "groq":   "llama-3.3-70b-versatile",
    "gemini": "gemini-2.5-flash",
    "ollama": "llama3.1:8b",
    "mistral": "mistral-large-latest",
}


def _preferred() -> str:
    """Return preferred provider from config, or auto-detect."""
    return get("oss.preferred_llm", "auto").lower()


def available_provider() -> str | None:
    """Return the first available provider, or None."""
    pref = _preferred()
    if pref != "auto":
        return pref if _provider_works(pref) else None

    for provider in ("groq", "gemini", "ollama", "mistral"):
        if _provider_works(provider):
            return provider
    return None


def _provider_works(provider: str) -> bool:
    if provider == "groq":
        return bool(os.getenv("GROQ_API_KEY"))
    if provider == "gemini":
        return bool(os.getenv("GOOGLE_API_KEY"))
    if provider == "mistral":
        return bool(os.getenv("MISTRAL_API_KEY"))
    if provider == "ollama":
        try:
            import httpx
            base = get("oss.ollama_base_url", "http://localhost:11434")
            r = httpx.get(f"{base}/api/tags", timeout=2.0)
            return r.status_code == 200
        except Exception:
            return False
    return False


def generate(
    prompt: str,
    system: str = "",
    model: str | None = None,
    provider: str | None = None,
    max_tokens: int = 2048,
) -> str:
    """Generate a completion. Auto-selects provider if not specified."""
    p = provider or available_provider()
    if p is None:
        raise RuntimeError(
            "No OSS LLM available. Set GROQ_API_KEY, GOOGLE_API_KEY, or start Ollama."
        )

    if p == "groq":
        return _groq(prompt, system, model or get("oss.groq_model", _DEFAULTS["groq"]), max_tokens)
    if p == "gemini":
        return _gemini(prompt, system, model or get("oss.gemini_model", _DEFAULTS["gemini"]), max_tokens)
    if p == "ollama":
        return _ollama(prompt, system, model or get("oss.ollama_model", _DEFAULTS["ollama"]), max_tokens)
    if p == "mistral":
        return _mistral(prompt, system, model or get("oss.mistral_model", _DEFAULTS["mistral"]), max_tokens)
    raise RuntimeError(f"Unknown provider: {p}")


def generate_multi(
    prompt: str,
    system: str = "",
    max_tokens: int = 1024,
) -> list[tuple[str, str]]:
    """Call all available providers in parallel. Returns [(provider, response), ...]."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    voices = get("oss.council_voices", [])
    if not voices:
        # Build from whatever is available
        voices = []
        for p in ("groq", "gemini", "ollama", "mistral"):
            if _provider_works(p):
                voices.append({"provider": p, "model": _DEFAULTS[p]})

    def call(voice: dict) -> tuple[str, str]:
        p = voice["provider"]
        m = voice.get("model", _DEFAULTS.get(p, ""))
        return (p, generate(prompt, system, model=m, provider=p, max_tokens=max_tokens))

    results = []
    with ThreadPoolExecutor(max_workers=len(voices)) as ex:
        futures = {ex.submit(call, v): v for v in voices}
        for fut in as_completed(futures):
            try:
                results.append(fut.result())
            except Exception as exc:
                v = futures[fut]
                logger.warning("OSS council voice %s failed: %s", v.get("provider"), exc)

    return results


# ── Provider implementations ──────────────────────────────────────────────────

def _groq(prompt: str, system: str, model: str, max_tokens: int) -> str:
    from groq import Groq
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    resp = client.chat.completions.create(model=model, messages=messages, max_tokens=max_tokens)
    return resp.choices[0].message.content or ""


def _gemini(prompt: str, system: str, model: str, max_tokens: int) -> str:
    import google.generativeai as genai
    genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
    full = f"{system}\n\n{prompt}" if system else prompt
    m = genai.GenerativeModel(model)
    resp = m.generate_content(full, generation_config={"max_output_tokens": max_tokens})
    return resp.text or ""


def _ollama(prompt: str, system: str, model: str, max_tokens: int) -> str:
    from openai import OpenAI
    base = get("oss.ollama_base_url", "http://localhost:11434") + "/v1"
    client = OpenAI(base_url=base, api_key="ollama")
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    resp = client.chat.completions.create(model=model, messages=messages, max_tokens=max_tokens)
    return resp.choices[0].message.content or ""


def _mistral(prompt: str, system: str, model: str, max_tokens: int) -> str:
    from mistralai import Mistral
    client = Mistral(api_key=os.getenv("MISTRAL_API_KEY"))
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    resp = client.chat.complete(model=model, messages=messages, max_tokens=max_tokens)
    return resp.choices[0].message.content or ""
