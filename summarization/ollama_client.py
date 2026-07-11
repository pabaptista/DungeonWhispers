import json
import urllib.request


def summarize(
    transcript: str,
    system_prompt: str,
    model: str = "gemma4-unsloth-nothink:latest",
    host: str = "http://localhost:11434",
    timeout: float = 300.0,
    num_ctx: int = 32768,
) -> str:
    payload = {
        "model": model,
        "prompt": transcript,
        "system": system_prompt,
        "stream": False,
        "think": False,
        # Ollama's runtime context window defaults to a few thousand tokens regardless of what the
        # model architecture supports, unless requested explicitly — a full session transcript can
        # easily exceed that default, silently truncating the input (confirmed: caused the model to
        # only "see" the tail of a transcript and echo it back instead of summarizing).
        "options": {"num_ctx": num_ctx},
    }
    req = urllib.request.Request(
        f"{host}/api/generate",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())["response"]


if __name__ == "__main__":
    import yaml

    from summarization.prompts import DND_RECAP_SYSTEM_PROMPT

    with open("config.yml") as f:
        cfg = yaml.safe_load(f)["ollama"]

    print(
        summarize(
            "The party defeated a young red dragon and found a +1 longsword.",
            DND_RECAP_SYSTEM_PROMPT,
            model=cfg["model"],
            host=cfg["host"],
        )
    )
