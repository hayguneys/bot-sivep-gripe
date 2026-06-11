"""Local LLM helper — routes to Ollama (http://localhost:11434)."""
import requests

OLLAMA_BASE = "http://localhost:11434"
DEFAULT_MODEL = "qwen3-coder-next:latest"


def chat(prompt: str, model: str = DEFAULT_MODEL, system: str = "") -> str:
    """Send a prompt to Ollama and return the response text."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    resp = requests.post(
        f"{OLLAMA_BASE}/api/chat",
        json={"model": model, "messages": messages, "stream": False},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["message"]["content"]


def models() -> list[str]:
    """Return names of locally available Ollama models."""
    resp = requests.get(f"{OLLAMA_BASE}/api/tags", timeout=10)
    resp.raise_for_status()
    return [m["name"] for m in resp.json().get("models", [])]
