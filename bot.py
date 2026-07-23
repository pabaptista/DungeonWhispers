import asyncio
import datetime as dt
import logging
import os
import urllib.error

import discord
import yaml

from naming import slugify, speaker_name
from recorder import session as rec_session
from recorder.sink import save_audio
from summarization.ollama_client import summarize
from summarization.prompts import DND_RECAP_SYSTEM_PROMPT, SHORT_RECAP_SYSTEM_PROMPT
from transcription.merge import format_transcript, merge_segments
from transcription.whisper_backend import transcribe

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("dungeonwhispers")


class _BenignVoiceErrorFilter(logging.Filter):
    """Downgrades known-benign py-cord voice errors from ERROR to WARNING (see CLAUDE.md
    gotchas — both confirmed non-fatal). Keeps them visible, just not flagged as failures."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno < logging.ERROR:
            return True
        message = record.getMessage()
        if message.startswith("Disconnected from voice... Reconnecting in"):
            record.levelno = logging.WARNING
            record.levelname = "WARNING"
        elif message == "An exception occurred while decoding voice packets" and record.exc_info:
            exc = record.exc_info[1]
            if isinstance(exc, AttributeError) and "RTPPacket" in str(exc):
                record.levelno = logging.WARNING
                record.levelname = "WARNING"
        return True


logging.getLogger("discord.voice.state").addFilter(_BenignVoiceErrorFilter())
logging.getLogger("discord.voice.receive.reader").addFilter(_BenignVoiceErrorFilter())


def _require(cfg: dict, *path: str) -> None:
    node = cfg
    for i, key in enumerate(path):
        if not isinstance(node, dict) or key not in node:
            raise ValueError(f"config.yml is missing required key: {'.'.join(path[: i + 1])}")
        node = node[key]


def validate_config(cfg: dict) -> None:
    _require(cfg, "discord", "bot_token")
    _require(cfg, "whisper", "model_size")
    _require(cfg, "whisper", "device")
    _require(cfg, "whisper", "compute_type")
    _require(cfg, "whisper", "language")  # may be null, but the key itself must exist
    _require(cfg, "ollama", "host")
    _require(cfg, "ollama", "model")
    for i, player in enumerate(cfg.get("players") or []):
        for key in ("discord_id", "player_name", "character_name"):
            if key not in player:
                raise ValueError(f"config.yml players[{i}] is missing required key: {key}")


def load_config() -> dict:
    with open("config.yml") as f:
        cfg = yaml.safe_load(f)
    validate_config(cfg)
    return cfg


CONFIG = load_config()
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
    try:
        await bot.change_presence(status=discord.Status.online, activity=IDLE_ACTIVITY)
    except Exception as e:
        log.error("Failed to set initial presence: %s", e)


def load_campaign_context() -> str:
    """Reads campaign_context.md fresh each call (no restart needed to update it between sessions)."""
    try:
        with open("campaign_context.md") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


@record.command(name="start", description="Join your voice channel and start recording.")
async def record_start(
    ctx: discord.ApplicationContext,
    name: discord.Option(str, "Name for this session, e.g. 'Session 10' (default: date/time)", required=False) = None,
):
    await ctx.defer()  # channel.connect() below can take a few seconds; ack before Discord's 3s interaction timeout

    global CONFIG, PLAYERS
    try:
        # Reread config.yml so new players/settings apply without a bot restart. Validated before
        # committing to the globals — a bad edit mid-session shouldn't crash the bot or wipe out a
        # working config.
        new_config = load_config()
    except (OSError, ValueError, yaml.YAMLError) as e:
        await ctx.respond(f"⚠️ config.yml failed to load, keeping the previous config (`{e}`).")
        return
    CONFIG = new_config
    PLAYERS = {p["discord_id"]: p for p in CONFIG.get("players") or []}

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

    unmapped = [f"{name} (`{uid}`)" for uid, name in session.member_names.items() if uid not in PLAYERS]
    if unmapped:
        log.warning("Not in config.yml: %s", ", ".join(unmapped))
        await ctx.channel.send(
            "⚠️ Not in `config.yml` yet: " + ", ".join(unmapped) + ". "
            "They'll show up in the transcript under their Discord display name instead of a character name. "
            "Add them under `players:` with `discord_id: <id above>` to fix."
        )


@record.command(name="status", description="Show the active recording session's status.")
async def record_status(ctx: discord.ApplicationContext):
    session = rec_session.get(ctx.guild.id)
    if not session:
        await ctx.respond("No recording in progress.")
        return

    elapsed = str(dt.datetime.now() - session.started_at).split(".")[0]
    title = f" **{session.name}**" if session.name else ""
    mapped = [n for uid, n in session.member_names.items() if uid in PLAYERS]
    unmapped = [f"{n} (`{uid}`)" for uid, n in session.member_names.items() if uid not in PLAYERS]

    lines = [f"🔴 Recording{title} — running for {elapsed}."]
    if mapped:
        lines.append("Mapped: " + ", ".join(mapped))
    if unmapped:
        lines.append("⚠️ Not in `config.yml`: " + ", ".join(unmapped))
    await ctx.respond("\n".join(lines))


@record.command(name="list", description="List past recorded sessions.")
async def record_list(ctx: discord.ApplicationContext):
    if not os.path.isdir("transcripts"):
        await ctx.respond("No sessions recorded yet.")
        return

    sessions = []
    for fname in sorted(os.listdir("transcripts"), reverse=True):
        if not fname.endswith(".md"):
            continue
        # Merged session files start with a top-level "# Title" header; the per-speaker debug
        # transcripts (also *.md, same directory) are raw segment dumps with no header — this is
        # what tells them apart, since session_id itself can contain underscores so filenames alone
        # aren't reliably distinguishable from the debug ones.
        with open(os.path.join("transcripts", fname)) as f:
            first_line = f.readline()
        if first_line.startswith("# "):
            sessions.append((fname, first_line[2:].strip()))

    if not sessions:
        await ctx.respond("No sessions recorded yet.")
        return

    shown = sessions[:15]
    lines = [f"**{title}** — `{fname}`" for fname, title in shown]
    message = "📼 Past sessions:\n" + "\n".join(lines)
    if len(sessions) > len(shown):
        message += f"\n...and {len(sessions) - len(shown)} more."
    if len(message) > 2000:
        message = message[:1997] + "..."
    await ctx.respond(message)


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
    except discord.sinks.OGGSinkError as exc:
        # AlignedOGGSink.format_audio() already isolates a per-speaker ffmpeg failure so it can't
        # abort py-cord's cleanup loop for the rest — this is a fallback for an OGGSinkError at
        # the loop level itself, logged loudly rather than letting the broader SinkException catch
        # below silently swallow it (OGGSinkError is a SinkException).
        log.error("ffmpeg failed while encoding recorded audio, some speakers' audio may be lost: %s", exc)
    except discord.sinks.SinkException:
        pass  # already cleaned up by AudioReader itself
    # force=True: disconnect(force=False) is a silent no-op if the connection state machine
    # isn't in exactly the "connected" state at that instant (e.g. mid voice-reconnect race) —
    # confirmed to otherwise leave py-cord's background reconnect task running indefinitely
    # against a channel we've already left, spamming "Could not connect to voice... Retrying...".
    try:
        await session.voice_client.disconnect(force=True)
    except Exception as e:
        # Recorded audio is already finalized above regardless (sink.cleanup() ran first), so
        # this doesn't block the transcribe/summarize pipeline — but a failure here can mean the
        # bot is still connected to the voice channel in an inconsistent state, so it shouldn't
        # be silent.
        log.error("Failed to disconnect voice client: %s", e)
        await ctx.channel.send(f"⚠️ Failed to cleanly disconnect from voice (`{e}`) — may need a manual kick/rejoin.")
    rec_session.end(ctx.guild.id)
    log.info("Audio finalized, disconnected from voice.")

    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    session_id = f"{timestamp}_{slugify(session.name)}" if session.name else timestamp
    display_name = session.name or timestamp
    audio_paths = save_audio(session.sink, session_id)
    log.info("Saved %d speaker track(s) to raw_audio/.", len(audio_paths))

    if not audio_paths:
        await ctx.channel.send(f"🔇 No one spoke during **{display_name}** — nothing to transcribe or summarize.")
        await bot.change_presence(status=discord.Status.online, activity=IDLE_ACTIVITY)
        log.info("Session '%s' had no speaker audio, nothing to do.", display_name)
        return

    os.makedirs("transcripts", exist_ok=True)

    whisper_cfg = CONFIG["whisper"]
    speaker_segments = {}
    for user_id, path in audio_paths.items():
        name = speaker_name(user_id, session.member_names, PLAYERS)
        log.info("Transcribing %s (%s)...", name, path)
        try:
            segments = await asyncio.to_thread(
                transcribe,
                path,
                model_size=whisper_cfg["model_size"],
                device=whisper_cfg["device"],
                compute_type=whisper_cfg["compute_type"],
                language=whisper_cfg["language"],
                hf_token=whisper_cfg.get("hf_token"),
                vad_filter=whisper_cfg.get("vad_filter", True),
            )
        except Exception as e:
            # One corrupt/empty track shouldn't discard everyone else's already-completed transcription.
            log.error("Transcription failed for %s (%s): %s", name, path, e)
            await ctx.channel.send(f"⚠️ Transcription failed for **{name}** — they'll be missing from this recap (`{e}`).")
            continue
        log.info("  -> %d segment(s).", len(segments))
        speaker_segments[name] = segments
        await ctx.channel.send(f"✅ Transcribed **{name}** ({len(segments)} segment(s)).")

        # debug: keep each speaker's raw transcript around, separate from the merged one below
        speaker_path = f"transcripts/{session_id}_{slugify(name)}.md"
        with open(speaker_path, "w") as f:
            f.write("\n".join(f"[{s.start:.1f}s -> {s.end:.1f}s] {s.text.strip()}" for s in segments) + "\n")

    if not speaker_segments:
        await ctx.channel.send(f"⚠️ Transcription failed for everyone in **{display_name}** — nothing to summarize.")
        await bot.change_presence(status=discord.Status.online, activity=IDLE_ACTIVITY)
        log.info("Session '%s': all speaker transcriptions failed.", display_name)
        return

    timeline = merge_segments(speaker_segments)
    transcript_text = format_transcript(timeline)
    log.info("Merged timeline: %d line(s) total.", len(timeline))

    ollama_cfg = CONFIG["ollama"]
    log.info("Summarizing via Ollama (%s)...", ollama_cfg["model"])
    await ctx.channel.send(f"🧠 All speakers transcribed ({len(timeline)} line(s)) — summarizing via Ollama now...")
    out_path = f"transcripts/{session_id}.md"
    campaign_context = load_campaign_context()
    recap_system_prompt = DND_RECAP_SYSTEM_PROMPT
    if campaign_context:
        recap_system_prompt += f"\n\n## Campaign Context (for reference/continuity only)\n\n{campaign_context}"
    try:
        summary = await asyncio.to_thread(
            summarize,
            transcript_text,
            recap_system_prompt,
            model=ollama_cfg["model"],
            host=ollama_cfg["host"],
            timeout=ollama_cfg.get("timeout", 300.0),
            num_ctx=ollama_cfg.get("num_ctx", 32768),
        )
        log.info("Summary generated (%d chars).", len(summary))
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        log.error("Ollama summarization failed: %s", e)
        with open(out_path, "w") as f:
            f.write(f"# {display_name}\n\n## Summary\n\n*Summarization failed: {e}*\n\n## Full Transcript\n\n{transcript_text}\n")
        await ctx.channel.send(
            f"⚠️ Couldn't reach Ollama to summarize (`{e}`). Transcript still saved to `{out_path}`."
        )
        await bot.change_presence(status=discord.Status.online, activity=IDLE_ACTIVITY)
        return

    with open(out_path, "w") as f:
        f.write(f"# {display_name}\n\n## Summary\n\n{summary}\n\n## Full Transcript\n\n{transcript_text}\n")
    log.info("Transcript saved to %s", out_path)

    # ponytail: raw audio + per-speaker transcripts kept for debugging, not deleted. Re-add cleanup once the
    # pipeline is trusted (see CLAUDE.md convention: "Delete raw audio after processing").

    # Discord caps messages at 2000 chars, and the full recap isn't meant to live in chat anyway (it goes to
    # transcripts/ for later import elsewhere) — so post a short "it worked" note instead of the full summary.
    try:
        short_summary = await asyncio.to_thread(
            summarize,
            summary,
            SHORT_RECAP_SYSTEM_PROMPT,
            model=ollama_cfg["model"],
            host=ollama_cfg["host"],
            timeout=ollama_cfg.get("timeout", 300.0),
            num_ctx=ollama_cfg.get("num_ctx", 32768),
        )
        short_summary = short_summary.strip()
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        log.warning("Short recap generation failed, falling back to a plain note: %s", e)
        short_summary = "(short recap unavailable, see full transcript)"

    message = f"✅ **{display_name}** complete.\n\n{short_summary}\n\nFull recap saved to `{out_path}`."
    if len(message) > 2000:
        message = message[:1997] + "..."
    await ctx.channel.send(message)
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
