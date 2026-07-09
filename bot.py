import datetime as dt
import os

import discord
import yaml

from recorder import session as rec_session
from recorder.sink import save_audio
from summarization.ollama_client import summarize
from summarization.prompts import DND_RECAP_SYSTEM_PROMPT
from transcription.merge import format_transcript, merge_segments
from transcription.whisper_backend import transcribe

with open("config.yml") as f:
    CONFIG = yaml.safe_load(f)

PLAYERS = {p["discord_id"]: p for p in CONFIG.get("players") or []}

intents = discord.Intents.default()
intents.voice_states = True
bot = discord.Bot(intents=intents)

record = discord.SlashCommandGroup("record", "Record and summarize the table's voice session.")


def speaker_name(discord_id: int, member_names: dict[int, str]) -> str:
    player = PLAYERS.get(discord_id)
    if player:
        return player["character_name"]
    return member_names.get(discord_id, f"Unknown ({discord_id})")


@record.command(name="start", description="Join your voice channel and start recording.")
async def record_start(ctx: discord.ApplicationContext):
    if ctx.author.voice is None:
        await ctx.respond("Join a voice channel first.")
        return
    if rec_session.get(ctx.guild.id):
        await ctx.respond("Already recording in this server.")
        return

    channel = ctx.author.voice.channel
    voice_client = await channel.connect()
    session = rec_session.start(ctx.guild.id, voice_client, ctx.channel)
    session.member_names = {m.id: m.display_name for m in channel.members if not m.bot}
    voice_client.start_recording(session.sink, None)

    await ctx.respond(
        f"\U0001f534 Recording started in **{channel.name}**. Everyone speaking in this channel is being "
        "recorded and transcribed for a session summary — speak now if that's not OK with you."
    )

    unmapped = [name for uid, name in session.member_names.items() if uid not in PLAYERS]
    if unmapped:
        await ctx.channel.send(
            "⚠️ Not in `config.yml` yet: " + ", ".join(unmapped) + ". "
            "They'll show up in the transcript under their Discord display name instead of a character name."
        )


@record.command(name="stop", description="Stop recording and post the session summary.")
async def record_stop(ctx: discord.ApplicationContext):
    session = rec_session.get(ctx.guild.id)
    if not session:
        await ctx.respond("No recording in progress.")
        return

    await ctx.respond("Stopping recording — transcribing and summarizing, this can take a while.")

    session.voice_client.stop_recording()
    session.sink.cleanup()  # runs ffmpeg per speaker (blocking); fine for a handful of players
    await session.voice_client.disconnect()
    rec_session.end(ctx.guild.id)

    session_id = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    audio_paths = save_audio(session.sink, session_id)

    whisper_cfg = CONFIG["whisper"]
    speaker_segments = {
        speaker_name(user_id, session.member_names): transcribe(
            path,
            model_size=whisper_cfg["model_size"],
            device=whisper_cfg["device"],
            compute_type=whisper_cfg["compute_type"],
            language=whisper_cfg["language"],
            hf_token=whisper_cfg.get("hf_token"),
        )
        for user_id, path in audio_paths.items()
    }

    timeline = merge_segments(speaker_segments)
    transcript_text = format_transcript(timeline)

    ollama_cfg = CONFIG["ollama"]
    summary = summarize(
        transcript_text,
        DND_RECAP_SYSTEM_PROMPT,
        model=ollama_cfg["model"],
        host=ollama_cfg["host"],
    )

    os.makedirs("transcripts", exist_ok=True)
    out_path = f"transcripts/{session_id}.md"
    with open(out_path, "w") as f:
        f.write(f"# Session {session_id}\n\n## Summary\n\n{summary}\n\n## Full Transcript\n\n{transcript_text}\n")

    for path in audio_paths.values():
        os.remove(path)

    await ctx.channel.send(f"**Session Recap**\n\n{summary}\n\nFull transcript saved to `{out_path}`.")


bot.add_application_command(record)

if __name__ == "__main__":
    bot.run(CONFIG["discord"]["bot_token"])
