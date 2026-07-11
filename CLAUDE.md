# DungeonWhispers

A self-hosted Discord bot that joins a voice channel during a tabletop RPG session, records each speaker separately, transcribes the audio, and produces an AI-generated session summary (recap of decisions, combat, loot, NPCs, cliffhangers).

Built for D&D tables, but generic enough for any voice-channel meeting/session that needs a transcript + summary.

## Status

Early-stage side project. Architecture is decided; implementation is in progress. Treat this file as the source of truth for design decisions — update it when decisions change, don't let it drift from the code.

## Tech Stack

- **Language:** Python 3.11+
- **Discord library:** `py-cord` (fork of discord.py with voice-receive support via `discord.sinks`) — installed from the `Pycord-Development/pycord@fix/voice-rec-2` branch (git), not PyPI. Stock py-cord's voice reception is broken by Discord's DAVE E2EE protocol; this branch (see [PR #3159](https://github.com/Pycord-Development/pycord/pull/3159)) fixes it and is confirmed working against a live voice channel. Switch `requirements.txt` back to a normal PyPI pin once that PR merges and ships.
- **Audio format:** OGG (`discord.sinks.OGGSink`) — py-cord decodes incoming Opus to PCM per speaker internally (for its mixing/filter support), then `OGGSink` shells out to **`ffmpeg`** to re-encode that PCM to OGG on `sink.cleanup()`. Requires `ffmpeg` on `PATH`; it is not a pip dependency.
- **Transcription:** `faster-whisper` (CTranslate2-based, not PyTorch) — avoids the CUDA/CPU torch wheel confusion of the original `openai-whisper` package, and is faster on CPU for long sessions.
- **Summarization:** Local LLM via [Ollama](https://ollama.com) (default model `gemma-4-E4B`, quantized GGUF) — no external API calls, entire pipeline (voice capture → transcription → summarization) runs fully offline/self-hosted.
- **Config:** YAML (`config.yml`, gitignored; `config.example.yml` committed)

## Architecture (high level)

```
Discord Voice Channel
  → py-cord voice listener (per-user audio sink, OGG/Opus)
  → raw_audio/{session_id}_{user_id}.ogg per speaker (session_id = timestamp, optionally + slugified session name)
  → faster-whisper transcription per track (timestamped segments), each also dumped to transcripts/{session_id}_{speaker}.md
  → merge all speakers' segments into one chronological timeline
  → tag each line with character name via config.yml (Discord ID → player → character), falling back to Discord display name if unmapped
  → local Ollama summarization (D&D-recap system prompt)
  → output: posted to a Discord text channel + saved as transcripts/{session_id}.md
```

## Project Structure

```
DungeonWhispers/
├── bot.py                  # Discord bot entrypoint, slash commands (/record start|stop|status|list)
├── naming.py                # Pure helpers (slugify, speaker_name) — split out of bot.py so they're
│                             #   testable without needing a real config.yml/Discord client
├── recorder/
│   ├── sink.py             # Voice capture, per-speaker aligned OGG sink
│   └── session.py          # Session lifecycle (start/stop/cleanup)
├── transcription/
│   ├── whisper_backend.py  # faster-whisper wrapper, per-track transcription
│   └── merge.py            # Merges + sorts per-speaker segments into one timeline
├── summarization/
│   ├── prompts.py          # D&D recap system prompt(s)
│   └── ollama_client.py    # Local Ollama call wrapper (default model: gemma-4-E4B)
├── config.example.yml      # Template: Discord ID → player name → character name mapping
├── config.yml              # Real config (gitignored)
├── campaign_context.example.md  # Template: setting/party/NPCs/threads fed to the LLM for continuity
├── campaign_context.md     # Real campaign context (gitignored, optional — skipped if absent)
├── raw_audio/              # Per-speaker OGG files (gitignored). NOT auto-deleted right now — see Conventions.
├── transcripts/            # Merged + per-speaker markdown transcripts, summaries (gitignored)
├── tests/                  # pytest — currently covers naming.py and transcription/merge.py (pure,
│                            #   no Discord/Ollama/config.yml needed). Run: `pytest`
├── requirements.txt
├── README.md
└── CLAUDE.md / AGENTS.md   # This file
```

## Conventions

- **Config over hardcoding:** Discord IDs, character name mappings, model sizes, and prompts live in `config.yml`, never hardcoded in source.
- **Config is validated, not just loaded:** `bot.py`'s `validate_config()` checks required keys exist (`discord.bot_token`, `whisper.*`, `ollama.host`/`model`, each `players[]` entry) and fails fast with a clear message — at startup via `load_config()`, and again on every `/record start` (see Commands below) where a validation failure keeps the previous working config instead of crashing the bot.
- **No secrets in git:** Discord bot token and `config.yml` are gitignored. Only `config.example.yml` (with placeholder values) is committed.
- **Pluggable transcription backend:** Keep `transcription/whisper_backend.py` behind a simple interface (e.g. `transcribe(audio_path, language=None) -> list[Segment]`) so a different backend (API-based, different local model) can be swapped in without touching the rest of the pipeline.
- **Delete raw audio after processing (currently disabled):** Multi-hour OGG tracks per speaker add up and should eventually be cleaned up after a transcript is generated and verified. **For now, while the recording/transcription pipeline is still being debugged, `bot.py` keeps `raw_audio/*.ogg` and the per-speaker debug transcripts in `transcripts/` — nothing is deleted.** Re-add cleanup (auto after N days, or a `--keep-audio` override) once the pipeline is trusted.
- **Type hints throughout:** Prefer explicit type hints on function signatures over relying on inference — makes the codebase easier to navigate for contributors.
- **Privacy/consent note:** Recording voice chats has all-party-consent implications in some jurisdictions. The README must carry a clear disclaimer; the bot should also announce itself (e.g. a join message) when it starts recording.

## Open Source Notes

- License: MIT (confirmed) — see `LICENSE` file
- `config.example.yml` must never contain real Discord IDs or tokens
- README should include: setup instructions, consent/privacy disclaimer, supported Whisper model sizes and their speed/accuracy tradeoffs, and a note on language support (Whisper supports ~99 languages; set `language` in config to skip auto-detection if the table is consistently one language)

## Known Gotchas

- `openai-whisper` pulls in PyTorch, and pip's default wheel often includes CUDA libraries even on CPU-only machines, causing a large unwanted download. `faster-whisper` avoids this entirely (no PyTorch dependency), which is the main reason it's the chosen backend here.
- Discord voice-receive is not part of vanilla `discord.py`; must use `py-cord` or `discord-ext-voice-recv`.
- Summarization requires the Ollama daemon running locally (`ollama serve`) with the configured model already pulled (`ollama pull <model>`); `ollama_client.py` does not auto-pull.
- **Discord's DAVE (End-to-End Encryption) protocol for voice channels breaks voice reception on stock py-cord** (PyPI releases, including 2.8.0) — `VoiceClient.start_recording`/`stop_recording` even emit a `RuntimeWarning` that reception "may not work as expected." Fixed by the `Pycord-Development/pycord@fix/voice-rec-2` branch ([PR #3159](https://github.com/Pycord-Development/pycord/pull/3159)), which `requirements.txt` installs directly from git — **confirmed working** against a live voice channel on 2026-07-10. Re-test after touching `requirements.txt`'s py-cord line. Track upstream status: https://github.com/Pycord-Development/pycord/issues/3139
- Whether `OGGSink.cleanup()` runs automatically when recording stops **depends on the py-cord build**: the PyPI `2.8.0` release has it as dead/commented code in `AudioReader._stop`, but the `fix/voice-rec-2` branch we install calls it automatically — calling it again then raises `SinkException("already finished writing")`. `bot.py` calls `session.sink.cleanup()` explicitly and swallows that specific exception so it works either way.
- `discord.Option(...)` slash-command params only ack the interaction on `ctx.respond()`; if real async work (e.g. `channel.connect()`) happens first, Discord's 3-second interaction timeout can expire and `ctx.respond()` raises `NotFound: Unknown interaction`. Call `await ctx.defer()` as the very first line of any command that does non-trivial work before its first response.
- `sink.audio_data` is keyed by `discord.User | discord.Member` in newer py-cord builds, not a plain `int` (though it may be a raw id in others) — `recorder/sink.py` normalizes with `getattr(user, "id", user)`. Don't assume the key is always an int.
- **Occasional `AttributeError: 'RTPPacket' object has no attribute 'type'` in logs during live recording** — confirmed non-fatal, 2026-07-11. Comes from `discord/voice/receive/reader.py`'s `is_rtcp()`, a naive check (`200 <= data[1] <= 204`) that misfires when an RTP packet's marker-bit + payload-type byte happens to collide with the RTCP type range (classic RTP/RTCP demux ambiguity, see RFC 5761). Discord sets the marker bit at the start of each talk-spurt (right after silence), so it's most likely to fire right when someone starts talking again. `AudioReader.callback()` catches it in a broad `except Exception`, logs, and moves on — worst case is one ~20ms audio frame misrouted to `feed_rtcp()` instead of the audio path, which `AlignedOGGSink`'s silence padding makes inaudible anyway. It's inside the pinned `fix/voice-rec-2` branch, not our code, so nothing to patch there — but `bot.py` installs a logging filter (`_BenignVoiceErrorFilter`) that downgrades this specific error from ERROR to WARNING so it doesn't read as a failure (see the voice-disconnect gotcha below for the other message it covers).
- **`TimeoutError` / "Disconnected from voice... Reconnecting in Xs" logged right after `/record stop`** — confirmed non-fatal, 2026-07-11. `voice_client.disconnect()` (called after `sink.cleanup()` in `bot.py`'s stop handler) tears down the voice websocket while py-cord's `_poll_ws` watchdog (`discord/voice/state.py`) is mid-`receive()`; its 30s timeout fires and its generic reconnect handler logs it as ERROR even though the disconnect was ours, intentional. No data risk — `sink.cleanup()` already finalized the audio before `disconnect()` is even called. `bot.py` installs a logging filter (`_BenignVoiceErrorFilter`) that downgrades this specific message, and the RTPPacket one above, from ERROR to WARNING so they don't read as failures; it checks exact message/exception text so it won't mask a genuinely different error at the same call site.
- **`voice_client.disconnect()` defaults to `force=False`, which can silently no-op** — confirmed 2026-07-11, root cause of a background reconnect loop left running after `/record stop` (manifested as recurring `"Could not connect to voice... Retrying..."` WARNINGs long after the session had ended). `VoiceClient.disconnect()` → `VoiceConnectionState.disconnect()` (`discord/voice/state.py:560`) starts with `if not force and not self.is_connected(): return` — if the connection state machine isn't in exactly `ConnectionFlowState.connected` at that instant (e.g. mid a voice-reconnect race, see the entry above), the whole teardown is skipped: the websocket isn't closed, the background `_poll_ws`/`_runner` task isn't stopped, `client.cleanup()` never runs. That task then keeps retrying to reconnect a channel we've already left, orphaned from our `RecordingSession` (already removed from `rec_session._active` by then), indefinitely with exponential backoff. `bot.py` now calls `voice_client.disconnect(force=True)` to guarantee full teardown runs regardless of transient connection state.
- **A `raw_audio/*_None.ogg` file, not a real speaker** — confirmed 2026-07-11, not the bot recording itself (the bot never sends outgoing audio, and `channel.members` filtering in `bot.py` already excludes bots from `member_names`). It's an SSRC that sent a packet before Discord's SPEAKING gateway event ever mapped it to a user — `PacketRouter.get_decoder()` (`discord/voice/receive/router.py`) auto-creates a decoder for any SSRC on first packet, and if `set_user_id()` hasn't run for it yet, `data.source` is `None`, so `Sink.write(data, None)` creates an `audio_data[None]` entry. Seen once so far, timing coincided with the voice-reconnect race described above, but the mechanism doesn't require that specific race. `recorder/sink.py`'s `save_audio()` now drops any `user is None` entry and logs a WARNING with its byte size instead of writing `raw_audio/{session_id}_None.ogg` and feeding it into transcription as `"Unknown (None)"`.
- **Summarization can silently echo the transcript back instead of summarizing it** — confirmed 2026-07-13, root cause was `ollama.num_ctx` never being set, not a prompt-wording problem. Ollama's runtime context window defaults to a few thousand tokens regardless of what the model architecture supports (`ollama show <model>` showing a large "context length" is the model's *max*, not what gets allocated per request) — a full session transcript easily exceeds that default and gets silently truncated from the front, so the model only "sees" the tail of the session and, having nothing to summarize, just echoes what little it can see back reformatted. Symptom looked like a prompt-following failure (verified by first trying to strengthen `DND_RECAP_SYSTEM_PROMPT` against verbatim echoing — didn't help) but the echoed text starting almost exactly at the transcript's tail was the tell. Fixed by passing `"options": {"num_ctx": ...}` explicitly in `summarization/ollama_client.py`'s `summarize()`, configurable via `ollama.num_ctx` in `config.yml` (default `32768`). Multi-hour sessions may need this raised further — bigger values cost more RAM/VRAM, so it's not defaulted to the model's max.
- **Whisper hallucinates repeated phrases on long silence** (e.g. `"Thank you."`, `"I'll be right back."` looped dozens of times) — confirmed 2026-07-11 on a real test transcript where a speaker stepped away for ~24 minutes. Whisper has no way to say "there's no speech here," so on a long silent stretch it decodes *something*, and once `condition_on_previous_text` feeds that hallucination back in as context, it can lock into a repeated-phrase loop. This got materially worse after `AlignedOGGSink` started padding real silence into speaker tracks (see below) — before that fix, a quiet stretch just wasn't in the file at all, so Whisper never saw it. Fixed in `transcription/whisper_backend.py`: `transcribe()` now passes `vad_filter=True` (Silero VAD strips non-speech regions before decoding — configurable via `whisper.vad_filter` in `config.yml`, default `true`) and `condition_on_previous_text=False` (stops one bad segment from compounding into a loop). Verified by re-transcribing the exact file that showed the loop — hallucinated repeats gone, real speech recovered.
- **Stock `Sink.write()` does not pad for silence** — it just appends each user's decoded PCM to their file as packets arrive, so a speaker's file only contains their own speaking time back-to-back, with no gap for time spent silent or disconnected. That makes per-speaker Whisper segment timestamps not comparable across speakers (`transcription/merge.py`'s chronological sort would be wrong), most visibly for someone who leaves and rejoins mid-session. `recorder/sink.py`'s `AlignedOGGSink` (used by `recorder/session.py` instead of `discord.sinks.OGGSink` directly) fixes this by padding every user's track with silence up to elapsed wall-clock time since the first packet of the session, before writing each chunk — so every track shares one clock and absence (silence, disconnect, joining late) is just recorded as silence rather than shrinking the file.
- `transcribe()` (faster-whisper) and `summarize()` (Ollama HTTP call) are both synchronous/blocking and can each run for many seconds to minutes. Calling them directly from a slash-command coroutine blocks the asyncio event loop, which starves Discord's gateway heartbeat — confirmed in testing (`Shard ID None heartbeat blocked for more than 20 seconds`), which risks Discord killing the connection on a real multi-hour session. `bot.py` wraps every call to `transcribe`, `summarize`, and `sink.cleanup` (also blocking, runs `ffmpeg`) in `asyncio.to_thread(...)`.

## Commands / Workflow

- `/record start [name]` — bot joins the caller's current voice channel and begins per-speaker recording. `name` is optional (e.g. `"Session 10"`); defaults to a timestamp if omitted. Rereads `config.yml` fresh on every call (no restart needed after adding a player) — a broken edit keeps the previous working config instead of crashing. Warns in the text channel if any speaker present isn't mapped in `config.yml` yet. Sets bot presence to 🔴 "Recording session" (status: dnd).
- `/record stop` — bot stops recording, triggers transcription → merge → summarization pipeline, posting per-speaker progress pings along the way, then the recap + transcript path in the text channel. Gracefully handles nobody having spoken, and isolates per-speaker transcription failures (one bad track doesn't lose everyone else's). Sets presence to 📝 "Summarizing session..." (status: idle) while working, then back to idle listening for `/record start` when done.
- `/record status` — shows the active session's name, elapsed running time, and which present speakers are mapped/unmapped in `config.yml`, without stopping the recording.
- `/record list` — lists past sessions (from `transcripts/*.md`), newest first.
- `python3 bot.py [-v|--verbose]` — `-v` raises terminal log level from `WARNING` to `INFO`, printing per-step progress (join, transcribe per speaker, merge, summarize, save, cleanup). Deliberately does *not* warm up the Whisper model at startup — the model (~1.5GB) stays resident once loaded (`_load_model` is `lru_cache`d, never evicted), so warming up at boot would hold that memory for the bot's entire uptime, including all idle time between sessions, just to save a few seconds on the first `/record stop`. Not worth it for a bot that may sit idle for days between game nights.
- `pytest` — runs the test suite (`tests/`, currently `naming.py` + `transcription/merge.py`).

## For AI Agents Working on This Repo

- Prioritize working, testable increments over large speculative refactors — this is built piece by piece (voice capture → transcription → merge → summarization) and each piece should be independently testable with sample OGG files before wiring into the next stage.
- When in doubt about a design decision not covered here, ask rather than assume — and update this file once a decision is made.
