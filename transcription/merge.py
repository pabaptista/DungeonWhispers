from dataclasses import dataclass

from transcription.whisper_backend import Segment


@dataclass
class TimelineEntry:
    start: float
    speaker: str
    text: str


def merge_segments(speaker_segments: dict[str, list[Segment]]) -> list[TimelineEntry]:
    timeline = [
        TimelineEntry(start=seg.start, speaker=speaker, text=seg.text.strip())
        for speaker, segs in speaker_segments.items()
        for seg in segs
    ]
    timeline.sort(key=lambda entry: entry.start)
    return timeline


def format_transcript(timeline: list[TimelineEntry]) -> str:
    return "\n".join(f"[{e.start:.1f}s] {e.speaker}: {e.text}" for e in timeline)
