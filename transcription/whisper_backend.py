from dataclasses import dataclass
from functools import lru_cache

from faster_whisper import WhisperModel


@dataclass
class Segment:
    start: float
    end: float
    text: str


@lru_cache(maxsize=4)
def _load_model(model_size: str, device: str, compute_type: str, hf_token: str | None) -> WhisperModel:
    return WhisperModel(model_size, device=device, compute_type=compute_type, use_auth_token=hf_token)


def transcribe(
    audio_path: str,
    model_size: str = "turbo",
    device: str = "cpu",
    compute_type: str = "int8",
    language: str | None = None,
    hf_token: str | None = None,
) -> list[Segment]:
    model = _load_model(model_size, device, compute_type, hf_token)
    segments, _info = model.transcribe(audio_path, beam_size=5, language=language)
    return [Segment(s.start, s.end, s.text) for s in segments]


if __name__ == "__main__":
    import yaml

    with open("config.yml") as f:
        cfg = yaml.safe_load(f)["whisper"]

    for seg in transcribe(
        "test.ogg",
        model_size=cfg["model_size"],
        device=cfg["device"],
        compute_type=cfg["compute_type"],
        language=cfg["language"],
        hf_token=cfg.get("hf_token"),
    ):
        print(f"[{seg.start:.2f}s -> {seg.end:.2f}s] {seg.text}")
