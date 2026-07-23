import io
import os

import discord.sinks.core as sinks_core
import discord.sinks.ogg as sinks_ogg
import pytest

import recorder.sink as sink_module
from recorder.sink import AlignedOGGSink, _FRAME_SIZE, save_audio

pytestmark = pytest.mark.usefixtures("_scratch_dir")


@pytest.fixture
def _scratch_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(sink_module, "_SCRATCH_DIR", str(tmp_path))
    yield tmp_path


@pytest.fixture(autouse=True)
def _fake_ffmpeg(monkeypatch):
    def fake_format_audio(self, audio):
        audio.file = io.BytesIO(b"fake-encoded")

    monkeypatch.setattr(sinks_ogg.OGGSink, "format_audio", fake_format_audio)


def test_write_creates_real_file_on_disk(tmp_path):
    s = AlignedOGGSink()
    s.write(b"\x00" * 8, "user-a")

    audio = s.audio_data["user-a"]
    assert isinstance(audio.file, io.IOBase)
    assert not isinstance(audio.file, io.BytesIO)
    scratch_path, _ = s._scratch_files[audio]
    assert os.path.dirname(scratch_path) == str(tmp_path)
    assert os.path.exists(scratch_path)


def test_silence_padding_layout_unchanged(monkeypatch):
    s = AlignedOGGSink()
    clock = [100.0]
    monkeypatch.setattr(sink_module.time, "perf_counter", lambda: clock[0])

    chunk = b"\x01\x02\x03\x04"  # one frame
    s.write(chunk, "user-a")

    clock[0] += 0.5  # advance elapsed time, forcing silence padding on next write
    s.write(chunk, "user-a")

    audio = s.audio_data["user-a"]
    audio.file.seek(0)
    data = audio.file.read()

    elapsed = 0.5
    expected_bytes = int(elapsed * sink_module._BYTES_PER_SECOND)
    expected_bytes -= expected_bytes % _FRAME_SIZE
    padding_len = expected_bytes - len(chunk)  # padding fills the gap between what's written and expected_bytes

    assert len(data) == len(chunk) + padding_len + len(chunk)
    assert data[: len(chunk)] == chunk
    assert data[len(chunk) : len(chunk) + padding_len] == b"\x00" * padding_len
    assert data[len(chunk) + padding_len :] == chunk


def test_scratch_file_removed_after_cleanup():
    s = AlignedOGGSink()
    s.write(b"\x00" * 8, "user-a")
    audio = s.audio_data["user-a"]
    scratch_path, _ = s._scratch_files[audio]

    s.cleanup()

    assert not os.path.exists(scratch_path)


def test_two_speakers_independent_scratch_files_both_cleaned_up():
    s = AlignedOGGSink()
    s.write(b"\x00" * 8, "user-a")
    s.write(b"\x00" * 8, "user-b")

    path_a, _ = s._scratch_files[s.audio_data["user-a"]]
    path_b, _ = s._scratch_files[s.audio_data["user-b"]]
    assert path_a != path_b
    assert os.path.exists(path_a)
    assert os.path.exists(path_b)

    s.cleanup()

    assert not os.path.exists(path_a)
    assert not os.path.exists(path_b)


def test_unresolved_ssrc_scratch_file_also_cleaned_up():
    s = AlignedOGGSink()
    s.write(b"\x00" * 8, None)
    scratch_path, _ = s._scratch_files[s.audio_data[None]]
    assert os.path.exists(scratch_path)

    s.cleanup()

    assert not os.path.exists(scratch_path)


def test_double_cleanup_raises_sink_exception_without_leaking():
    s = AlignedOGGSink()
    s.write(b"\x00" * 8, "user-a")

    s.cleanup()
    with pytest.raises(sinks_core.SinkException):
        s.cleanup()


def test_write_remembers_scratch_open_failure_and_stops_retrying(tmp_path, monkeypatch):
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")  # os.makedirs(_SCRATCH_DIR) fails: path exists as a file
    monkeypatch.setattr(sink_module, "_SCRATCH_DIR", str(blocker))

    calls = []
    real_makedirs = os.makedirs

    def counting_makedirs(path, exist_ok=False):
        calls.append(path)
        return real_makedirs(path, exist_ok=exist_ok)

    monkeypatch.setattr(sink_module.os, "makedirs", counting_makedirs)

    s = AlignedOGGSink()
    s.write(b"\x00" * 8, "user-a")
    s.write(b"\x00" * 8, "user-a")  # second packet for the same speaker

    assert "user-a" not in s.audio_data
    assert "user-a" in s._failed_users
    assert len(calls) == 1  # not retried on the second packet


def test_format_audio_marks_failed_encode_but_still_cleans_up_scratch():
    def failing_format_audio(self, audio):
        raise sinks_ogg.OGGSinkError("ffmpeg was not found.")

    import discord.sinks.ogg as ogg_module

    orig = ogg_module.OGGSink.format_audio
    ogg_module.OGGSink.format_audio = failing_format_audio
    try:
        s = AlignedOGGSink()
        s.write(b"\x00" * 8, "user-a")
        scratch_path, _ = s._scratch_files[s.audio_data["user-a"]]

        s.cleanup()  # base Sink.cleanup() loop must not abort here

        assert s.audio_data["user-a"].file is None
        assert not os.path.exists(scratch_path)
    finally:
        ogg_module.OGGSink.format_audio = orig


def test_save_audio_writes_files_and_returns_int_keyed_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    class FakeUser:
        id = 111

    s = AlignedOGGSink()
    s.write(b"\x00" * 8, FakeUser())
    s.cleanup()

    paths = save_audio(s, "session1")

    assert paths == {111: "raw_audio/session1_111.ogg"}
    with open("raw_audio/session1_111.ogg", "rb") as f:
        assert f.read() == b"fake-encoded"


def test_save_audio_drops_unresolved_ssrc(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    s = AlignedOGGSink()
    s.write(b"\x00" * 8, None)
    s.cleanup()

    paths = save_audio(s, "session1")

    assert paths == {}
    assert os.listdir("raw_audio") == []


def test_save_audio_skips_speaker_with_failed_encode(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def failing_format_audio(self, audio):
        raise sinks_ogg.OGGSinkError("ffmpeg was not found.")

    monkeypatch.setattr(sinks_ogg.OGGSink, "format_audio", failing_format_audio)

    s = AlignedOGGSink()
    s.write(b"\x00" * 8, "user-a")
    s.cleanup()

    paths = save_audio(s, "session1")

    assert paths == {}
    assert os.listdir("raw_audio") == []
