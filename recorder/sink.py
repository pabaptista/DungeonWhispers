import io
import logging
import os
import time
import uuid

import discord
from discord.sinks.core import AudioData, Filters
from discord.voice.packets import VoiceData

from naming import resolve_user_id

log = logging.getLogger("dungeonwhispers")

# Discord voice receive is always 48kHz/16-bit/stereo PCM (matches the "-ar 48000 -ac 2 -f s16le"
# ffmpeg input args OGGSink.format_audio() uses) — needed to convert a silence duration into a
# byte count.
_BYTES_PER_SECOND = 48000 * 2 * 2
_FRAME_SIZE = 4  # 2 bytes/sample * 2 channels; pad only to whole-sample boundaries

# Scratch dir for raw per-speaker PCM during a live recording (module-level so tests can
# monkeypatch it). Deliberately not /tmp or tempfile.gettempdir(): those are commonly tmpfs
# (RAM-backed) on Linux, which would defeat the point of writing to disk instead of memory.
_SCRATCH_DIR = os.path.join("raw_audio", "scratch")


class AlignedOGGSink(discord.sinks.OGGSink):
    """OGGSink that keeps every speaker's track on one shared clock.

    py-cord's stock Sink.write() just appends each user's decoded PCM to their file as packets
    arrive — it does not pad for silence. That means a speaker's file only contains their own
    speaking time, back-to-back, with no gaps for time spent silent or disconnected. Two
    speakers' per-file segment timestamps (from Whisper) are then not comparable, so
    transcription.merge.merge_segments's chronological sort is wrong for anyone who paused,
    left, or joined late — most visibly on rejoin, but really any silence longer than Discord's
    trailing frames causes drift.

    This subclass pads each user's track with silence up to "elapsed time since the first
    packet of the whole session", before writing each chunk of real audio. That anchors every
    track to the same t=0, so a speaker's silence, gap, or absence (including a disconnect and
    later rejoin) is simply recorded as silence rather than shrinking the file — Whisper segment
    offsets end up directly comparable across speakers with no extra bookkeeping.
    """

    def __init__(self, *, filters=None):
        super().__init__(filters=filters)
        self._session_start: float | None = None
        self._bytes_written: dict[object, int] = {}
        self._scratch_token = uuid.uuid4().hex  # unique per sink instance, avoids collisions across concurrent guild sessions
        self._scratch_seq = 0  # per-speaker counter for unique filenames
        self._scratch_files: dict[AudioData, tuple[str, io.IOBase]] = {}
        self._failed_users: set[object] = set()  # scratch-file open already failed once; don't retry every packet

    @Filters.container
    def write(self, data: VoiceData | bytes, user) -> None:
        if user in self._failed_users:
            return

        pcm_data = data.pcm if isinstance(data, VoiceData) else data

        if self._session_start is None:
            self._session_start = time.perf_counter()

        if user not in self.audio_data:
            user_key = resolve_user_id(user)
            scratch_path = os.path.join(_SCRATCH_DIR, f"{self._scratch_token}_{self._scratch_seq}_{user_key}.pcm")
            self._scratch_seq += 1
            try:
                os.makedirs(_SCRATCH_DIR, exist_ok=True)
                raw_file = open(scratch_path, "w+b")  # write+read+seek: AudioData.cleanup() seeks to 0, format_audio() reads it back
            except OSError:
                # Disk full, permission error, etc. Isolate the failure to this one speaker
                # rather than letting it propagate out of write() and kill the whole session's
                # recording — same "one bad track doesn't lose everyone else's" posture bot.py
                # already applies to per-speaker transcription failures. Remembered so we don't
                # retry (and re-log a full traceback) on every subsequent ~20ms packet for them.
                self._failed_users.add(user)
                log.error("Could not create scratch file for speaker %s; dropping their audio for this session.", user_key, exc_info=True)
                return
            audio = AudioData(raw_file)
            self.audio_data[user] = audio
            self._bytes_written[user] = 0
            self._scratch_files[audio] = (scratch_path, raw_file)

        elapsed = time.perf_counter() - self._session_start
        expected_bytes = int(elapsed * _BYTES_PER_SECOND)
        expected_bytes -= expected_bytes % _FRAME_SIZE

        written = self._bytes_written[user]
        if expected_bytes > written:
            # Write silence in 64KB chunks to avoid a temporary ~29 MB+ bytes object
            # for a silent speaker (b"\x00" * N would otherwise spike RSS proportionally).
            silence_size = expected_bytes - written
            while silence_size > 0:
                chunk = min(silence_size, 65536)
                self.audio_data[user].write(b"\x00" * chunk)
                silence_size -= chunk
            written = expected_bytes

        self.audio_data[user].write(pcm_data)
        self._bytes_written[user] = written + len(pcm_data)

    def format_audio(self, audio: AudioData) -> None:
        scratch = self._scratch_files.pop(audio, None)
        try:
            super().format_audio(audio)  # ffmpeg encode; reassigns audio.file to the small in-memory OGG BytesIO
        except discord.sinks.OGGSinkError as exc:
            # base Sink.cleanup() does `for file in self.audio_data.values(): file.cleanup();
            # self.format_audio(file)` — letting this propagate would abort that loop and skip
            # format_audio() for every speaker after this one in iteration order, leaking their
            # scratch files too. Isolate to this one speaker instead; save_audio() drops anyone
            # left with audio.file is None.
            log.error("ffmpeg failed to encode audio for a speaker; their track will be dropped: %s", exc)
            audio.file = None
        finally:
            # Always clean up the scratch file, even if ffmpeg raised (e.g. missing binary) —
            # otherwise the raw PCM leaks on disk with no reference left to remove it later.
            if scratch is not None:
                scratch_path, raw_file = scratch
                try:
                    raw_file.close()
                except OSError as exc:
                    log.warning("Failed to close scratch file %s: %s", scratch_path, exc)
                try:
                    os.remove(scratch_path)
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    log.warning("Failed to remove scratch file %s: %s", scratch_path, exc)


def save_audio(sink: discord.sinks.OGGSink, session_id: str) -> dict[int, str]:
    """Writes each speaker's recorded audio to raw_audio/. Call after sink.cleanup()."""
    os.makedirs("raw_audio", exist_ok=True)
    paths: dict[int, str] = {}
    for user, audio in sink.audio_data.items():
        if user is None:
            # Unresolved SSRC: a packet arrived before Discord's SPEAKING event ever mapped it to
            # a user (e.g. during the voice-reconnect race some py-cord builds have). Can't be
            # attributed to anyone, so drop it rather than feed "Unknown (None)" into the transcript.
            data = audio.file.read()
            log.warning("Discarding %d byte(s) of audio from an unresolved speaker (unmapped SSRC).", len(data))
            continue
        if audio.file is None:
            # format_audio() marks a speaker this way when their ffmpeg encode failed (see
            # AlignedOGGSink.format_audio) — their scratch PCM is already gone, nothing to save.
            log.warning("Skipping speaker %s: audio encoding failed for this session.", resolve_user_id(user))
            continue
        user_id = resolve_user_id(user)  # py-cord versions key audio_data by User/Member or by raw int id
        assert user_id is not None  # guaranteed by the `user is None` check above; keeps `paths` honestly `dict[int, str]`
        path = f"raw_audio/{session_id}_{user_id}.ogg"
        with open(path, "wb") as f:
            f.write(audio.file.read())
        paths[user_id] = path
    return paths
