import os

import discord


def save_audio(sink: discord.sinks.OGGSink, session_id: str) -> dict[int, str]:
    """Writes each speaker's recorded audio to raw_audio/. Call after sink.cleanup()."""
    os.makedirs("raw_audio", exist_ok=True)
    paths: dict[int, str] = {}
    for user, audio in sink.audio_data.items():
        user_id = getattr(user, "id", user)  # py-cord versions key audio_data by User/Member or by raw int id
        path = f"raw_audio/{session_id}_{user_id}.ogg"
        with open(path, "wb") as f:
            f.write(audio.file.read())
        paths[user_id] = path
    return paths
