import json
import urllib.error
import urllib.request


def is_up(host: str, timeout: float = 5.0) -> bool:
    """True if llama-server is reachable and has a model loaded."""
    try:
        with urllib.request.urlopen(f"{host}/health", timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        return False


def summarize(
    transcript: str,
    system_prompt: str,
    model: str = "local",
    host: str = "http://localhost:8080",
    timeout: float = 300.0,
) -> str:
    # llama.cpp's server (llama-server) exposes an OpenAI-compatible /v1/chat/completions
    # endpoint. Unlike Ollama, its context window is fixed at server launch (`-c <n>`), not
    # sent per request — start llama-server with enough context to fit a full session
    # transcript, or the model will only "see" the tail of it (see CLAUDE.md gotchas).
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": transcript},
        ],
        "stream": False,
    }
    req = urllib.request.Request(
        f"{host}/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())["choices"][0]["message"]["content"]


if __name__ == "__main__":
    import yaml

    from summarization.prompts import DND_RECAP_SYSTEM_PROMPT

    with open("config.yml") as f:
        cfg = yaml.safe_load(f)["llm"]

    print(
        summarize(
            "The party defeated a young red dragon and found a +1 longsword.",
            DND_RECAP_SYSTEM_PROMPT,
            model=cfg["model"],
            host=cfg["host"],
        )
    )
