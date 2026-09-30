"""
Asks each AI assistant a question through its official API.

Every function has the same shape: ask(question_text) -> {"text": str, "sources": [urls]}.
No scraping of chat websites. Official APIs only, which keeps us inside each provider's terms.

Set the API keys as environment variables to use live mode:
  OPENAI_API_KEY, GOOGLE_API_KEY, ANTHROPIC_API_KEY, PERPLEXITY_API_KEY
Missing keys are skipped with a note, so you can run with just one.

Copilot: Microsoft does not offer a public consumer Copilot API, so Copilot appears in the demo
sample data only. Adding any assistant is one function with this same shape, registered in ASSISTANTS.

Requests use only the standard library (urllib) so there is nothing to install.
"""

from __future__ import annotations

import json
import os
import urllib.request

SYSTEM = "You are a helpful shopping assistant. Answer in 2 to 4 sentences with specific product names and prices."


def _post(url: str, headers: dict, body: dict, timeout: int = 60) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def ask_openai(q: str) -> dict:
    key = os.environ["OPENAI_API_KEY"]
    out = _post("https://api.openai.com/v1/chat/completions", {"Authorization": f"Bearer {key}"},
                {"model": "gpt-4o-mini", "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": q}]})
    return {"text": out["choices"][0]["message"]["content"], "sources": []}


def ask_anthropic(q: str) -> dict:
    key = os.environ["ANTHROPIC_API_KEY"]
    out = _post("https://api.anthropic.com/v1/messages",
                {"x-api-key": key, "anthropic-version": "2023-06-01"},
                {"model": "claude-3-5-haiku-latest", "max_tokens": 400, "system": SYSTEM, "messages": [{"role": "user", "content": q}]})
    return {"text": "".join(b.get("text", "") for b in out["content"]), "sources": []}


def ask_gemini(q: str) -> dict:
    key = os.environ["GOOGLE_API_KEY"]
    out = _post(f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={key}", {},
                {"contents": [{"parts": [{"text": SYSTEM + "\n\n" + q}]}]})
    return {"text": out["candidates"][0]["content"]["parts"][0]["text"], "sources": []}


def ask_perplexity(q: str) -> dict:
    """Perplexity returns the web pages it used, which powers Source Tracing."""
    key = os.environ["PERPLEXITY_API_KEY"]
    out = _post("https://api.perplexity.ai/chat/completions", {"Authorization": f"Bearer {key}"},
                {"model": "sonar", "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": q}]})
    return {"text": out["choices"][0]["message"]["content"], "sources": out.get("citations", [])}


ASSISTANTS = {
    "chatgpt": ("OPENAI_API_KEY", ask_openai),
    "claude": ("ANTHROPIC_API_KEY", ask_anthropic),
    "gemini": ("GOOGLE_API_KEY", ask_gemini),
    "perplexity": ("PERPLEXITY_API_KEY", ask_perplexity),
}


def available() -> list[str]:
    return [name for name, (env, _) in ASSISTANTS.items() if os.environ.get(env)]
