from dataclasses import dataclass, field

import discord

from recorder.sink import AlignedOGGSink


@dataclass
class RecordingSession:
    guild_id: int
    voice_client: discord.VoiceClient
    text_channel: discord.TextChannel
    sink: discord.sinks.OGGSink = field(default_factory=AlignedOGGSink)
    member_names: dict[int, str] = field(default_factory=dict)
    name: str | None = None


_active: dict[int, RecordingSession] = {}


def start(guild_id: int, voice_client: discord.VoiceClient, text_channel: discord.TextChannel) -> RecordingSession:
    if guild_id in _active:
        raise RuntimeError("A recording is already in progress in this server.")
    session = RecordingSession(guild_id, voice_client, text_channel)
    _active[guild_id] = session
    return session


def get(guild_id: int) -> RecordingSession | None:
    return _active.get(guild_id)


def end(guild_id: int) -> None:
    _active.pop(guild_id, None)
