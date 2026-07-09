import json
import urllib.request


def summarize(
    transcript: str,
    system_prompt: str,
    model: str = "gemma4-unsloth-nothink:latest",
    host: str = "http://localhost:11434",
) -> str:
    payload = {
        "model": model,
        "prompt": transcript,
        "system": system_prompt,
        "stream": False,
    }
    req = urllib.request.Request(
        f"{host}/api/generate",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
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
