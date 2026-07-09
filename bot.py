import asyncio
import datetime as dt
import logging
import os
import re

import discord
import yaml

from recorder import session as rec_session
from recorder.sink import save_audio
from summarization.ollama_client import summarize
from summarization.prompts import DND_RECAP_SYSTEM_PROMPT
from transcription.merge import format_transcript, merge_segments
from transcription.whisper_backend import transcribe

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("dungeonwhispers")

with open("config.yml") as f:
    CONFIG = yaml.safe_load(f)

PLAYERS = {p["discord_id"]: p for p in CONFIG.get("players") or []}

intents = discord.Intents.default()
intents.members = True
intents.voice_states = True
bot = discord.Bot(intents=intents)

record = discord.SlashCommandGroup("record", "Record and summarize the table's voice session.")

IDLE_ACTIVITY = discord.Activity(type=discord.ActivityType.listening, name="/record start")


@bot.event
async def on_ready():
    log.info("Logged in as %s — ready.", bot.user)
    await bot.change_presence(status=discord.Status.online, activity=IDLE_ACTIVITY)


def slugify(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()


def speaker_name(discord_id: int, member_names: dict[int, str]) -> str:
    player = PLAYERS.get(discord_id)
    if player:
        return player["character_name"]
    return member_names.get(discord_id, f"Unknown ({discord_id})")


@record.command(name="start", description="Join your voice channel and start recording.")
async def record_start(
    ctx: discord.ApplicationContext,
    name: discord.Option(str, "Name for this session, e.g. 'Session 10' (default: date/time)", required=False) = None,
):
    await ctx.defer()  # channel.connect() below can take a few seconds; ack before Discord's 3s interaction timeout

    if ctx.author.voice is None:
        await ctx.respond("Join a voice channel first.")
        return
    if rec_session.get(ctx.guild.id):
        await ctx.respond("Already recording in this server.")
        return

    channel = ctx.author.voice.channel
    log.info("Joining '%s' in guild '%s' (session: %s)", channel.name, ctx.guild.name, name or "unnamed")
    voice_client = await channel.connect()
    session = rec_session.start(ctx.guild.id, voice_client, ctx.channel)
    session.name = name
    session.member_names = {m.id: m.display_name for m in channel.members if not m.bot}
    voice_client.start_recording(session.sink, None)
    await bot.change_presence(status=discord.Status.dnd, activity=discord.Game(name="\U0001f534 Recording session"))
    log.info("Recording started with %d speaker(s) present.", len(session.member_names))

    title = f" **{name}**" if name else ""
    await ctx.respond(
        f"\U0001f534 Recording{title} started in **{channel.name}**. Everyone speaking in this channel is being "
        "recorded and transcribed for a session summary — speak now if that's not OK with you."
    )

    unmapped = [name for uid, name in session.member_names.items() if uid not in PLAYERS]
    if unmapped:
        log.warning("Not in config.yml: %s", ", ".join(unmapped))
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

    await ctx.respond("⏹️ Recording stopped. Transcribing and summarizing — this can take a while.")
    await bot.change_presence(status=discord.Status.idle, activity=discord.Game(name="\U0001f4dd Summarizing session..."))
    log.info("Stopping recording for guild '%s'.", ctx.guild.name)

    session.voice_client.stop_recording()
    try:
        await asyncio.to_thread(session.sink.cleanup)  # runs ffmpeg per speaker; some py-cord versions do this already
    except discord.sinks.SinkException:
        pass  # already cleaned up by AudioReader itself
    await session.voice_client.disconnect()
    rec_session.end(ctx.guild.id)
    log.info("Audio finalized, disconnected from voice.")

    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    session_id = f"{timestamp}_{slugify(session.name)}" if session.name else timestamp
    display_name = session.name or timestamp
    audio_paths = save_audio(session.sink, session_id)
    log.info("Saved %d speaker track(s) to raw_audio/.", len(audio_paths))

    os.makedirs("transcripts", exist_ok=True)

    whisper_cfg = CONFIG["whisper"]
    speaker_segments = {}
    for user_id, path in audio_paths.items():
        name = speaker_name(user_id, session.member_names)
        log.info("Transcribing %s (%s)...", name, path)
        segments = await asyncio.to_thread(
            transcribe,
            path,
            model_size=whisper_cfg["model_size"],
            device=whisper_cfg["device"],
            compute_type=whisper_cfg["compute_type"],
            language=whisper_cfg["language"],
            hf_token=whisper_cfg.get("hf_token"),
        )
        log.info("  -> %d segment(s).", len(segments))
        speaker_segments[name] = segments

        # debug: keep each speaker's raw transcript around, separate from the merged one below
        speaker_path = f"transcripts/{session_id}_{slugify(name)}.md"
        with open(speaker_path, "w") as f:
            f.write("\n".join(f"[{s.start:.1f}s -> {s.end:.1f}s] {s.text.strip()}" for s in segments) + "\n")

    timeline = merge_segments(speaker_segments)
    transcript_text = format_transcript(timeline)
    log.info("Merged timeline: %d line(s) total.", len(timeline))

    ollama_cfg = CONFIG["ollama"]
    log.info("Summarizing via Ollama (%s)...", ollama_cfg["model"])
    summary = await asyncio.to_thread(
        summarize,
        transcript_text,
        DND_RECAP_SYSTEM_PROMPT,
        model=ollama_cfg["model"],
        host=ollama_cfg["host"],
    )
    log.info("Summary generated (%d chars).", len(summary))

    out_path = f"transcripts/{session_id}.md"
    with open(out_path, "w") as f:
        f.write(f"# {display_name}\n\n## Summary\n\n{summary}\n\n## Full Transcript\n\n{transcript_text}\n")
    log.info("Transcript saved to %s", out_path)

    # ponytail: raw audio + per-speaker transcripts kept for debugging, not deleted. Re-add cleanup once the
    # pipeline is trusted (see CLAUDE.md convention: "Delete raw audio after processing").

    await ctx.channel.send(f"**Session Recap — {display_name}**\n\n{summary}\n\nFull transcript saved to `{out_path}`.")
    await bot.change_presence(status=discord.Status.online, activity=IDLE_ACTIVITY)
    log.info("Session '%s' complete.", display_name)


bot.add_application_command(record)

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("-v", "--verbose", action="store_true", help="print progress info to the terminal")
    args = parser.parse_args()
    if args.verbose:
        log.setLevel(logging.INFO)

    bot.run(CONFIG["discord"]["bot_token"])
