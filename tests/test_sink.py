import io
import os

import discord.sinks.core as sinks_core
import discord.sinks.ogg as sinks_ogg
import pytest

import recorder.sink as sink_module
from recorder.sink import AlignedOGGSink, _FRAME_SIZE

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
