from transcription.merge import format_transcript, merge_segments
from transcription.whisper_backend import Segment


def test_merge_segments_sorts_across_speakers_by_start_time():
    speaker_segments = {
        "Alice": [Segment(start=5.0, end=6.0, text="second")],
        "Bob": [Segment(start=0.0, end=1.0, text="first")],
    }
    timeline = merge_segments(speaker_segments)
    assert [e.speaker for e in timeline] == ["Bob", "Alice"]
    assert [e.text for e in timeline] == ["first", "second"]


def test_merge_segments_strips_whitespace_from_text():
    speaker_segments = {"Alice": [Segment(start=0.0, end=1.0, text="  hello  ")]}
    timeline = merge_segments(speaker_segments)
    assert timeline[0].text == "hello"


def test_merge_segments_empty_input():
    assert merge_segments({}) == []


def test_merge_segments_interleaves_multiple_speakers():
    speaker_segments = {
        "Alice": [Segment(0.0, 1.0, "a1"), Segment(2.0, 3.0, "a2")],
        "Bob": [Segment(1.5, 2.0, "b1")],
    }
    timeline = merge_segments(speaker_segments)
    assert [e.text for e in timeline] == ["a1", "b1", "a2"]


def test_format_transcript_renders_time_speaker_text():
    speaker_segments = {"Alice": [Segment(start=4.4, end=5.0, text="hello")]}
    timeline = merge_segments(speaker_segments)
    assert format_transcript(timeline) == "[4.4s] Alice: hello"


def test_format_transcript_empty_timeline():
    assert format_transcript([]) == ""
