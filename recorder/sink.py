import io
import logging
import os
import time

import discord
from discord.sinks.core import AudioData, Filters
from discord.voice.packets import VoiceData

log = logging.getLogger("dungeonwhispers")

# Discord voice receive is always 48kHz/16-bit/stereo PCM (matches the "-ar 48000 -ac 2 -f s16le"
# ffmpeg input args OGGSink.format_audio() uses) — needed to convert a silence duration into a
# byte count.
_BYTES_PER_SECOND = 48000 * 2 * 2
_FRAME_SIZE = 4  # 2 bytes/sample * 2 channels; pad only to whole-sample boundaries


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

    @Filters.container
    def write(self, data: VoiceData | bytes, user) -> None:
        pcm_data = data.pcm if isinstance(data, VoiceData) else data

        if self._session_start is None:
            self._session_start = time.perf_counter()

        if user not in self.audio_data:
            self.audio_data[user] = AudioData(io.BytesIO())
            self._bytes_written[user] = 0

        elapsed = time.perf_counter() - self._session_start
        expected_bytes = int(elapsed * _BYTES_PER_SECOND)
        expected_bytes -= expected_bytes % _FRAME_SIZE

        written = self._bytes_written[user]
        if expected_bytes > written:
            self.audio_data[user].write(b"\x00" * (expected_bytes - written))
            written = expected_bytes

        self.audio_data[user].write(pcm_data)
        self._bytes_written[user] = written + len(pcm_data)


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
        user_id = getattr(user, "id", user)  # py-cord versions key audio_data by User/Member or by raw int id
        path = f"raw_audio/{session_id}_{user_id}.ogg"
        with open(path, "wb") as f:
            f.write(audio.file.read())
        paths[user_id] = path
    return paths
