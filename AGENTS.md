# DungeonWhispers

A self-hosted Discord bot that joins a voice channel during a tabletop RPG session, records each speaker separately, transcribes the audio, and produces an AI-generated session summary (recap of decisions, combat, loot, NPCs, cliffhangers).

Built for D&D tables, but generic enough for any voice-channel meeting/session that needs a transcript + summary.

## Status

Early-stage side project. Architecture is decided; implementation is in progress. Treat this file as the source of truth for design decisions — update it when decisions change, don't let it drift from the code.

## Tech Stack

- **Language:** Python 3.11+
- **Discord library:** `py-cord` (fork of discord.py with voice-receive support via `discord.sinks`)
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
├── bot.py                  # Discord bot entrypoint, slash commands (/record start, /record stop)
├── recorder/
│   ├── sink.py             # Voice capture, per-speaker OGG sink
│   └── session.py          # Session lifecycle (start/stop/cleanup)
├── transcription/
│   ├── whisper_backend.py  # faster-whisper wrapper, per-track transcription
│   └── merge.py            # Merges + sorts per-speaker segments into one timeline
├── summarization/
│   ├── prompts.py          # D&D recap system prompt(s)
│   └── ollama_client.py    # Local Ollama call wrapper (default model: gemma-4-E4B)
├── config.example.yml      # Template: Discord ID → player name → character name mapping
├── config.yml              # Real config (gitignored)
├── raw_audio/              # Per-speaker OGG files (gitignored). NOT auto-deleted right now — see Conventions.
├── transcripts/            # Merged + per-speaker markdown transcripts, summaries (gitignored)
├── requirements.txt
├── README.md
└── CLAUDE.md / AGENTS.md   # This file
```

## Conventions

- **Config over hardcoding:** Discord IDs, character name mappings, model sizes, and prompts live in `config.yml`, never hardcoded in source.
- **No secrets in git:** Discord bot token and `config.yml` are gitignored. Only `config.example.yml` (with placeholder values) is committed.
- **Pluggable transcription backend:** Keep `transcription/whisper_backend.py` behind a simple interface (e.g. `transcribe(audio_path, language=None) -> list[Segment]`) so a different backend (API-based, different local model) can be swapped in without touching the rest of the pipeline.
- **Delete raw audio after processing (currently disabled):** Multi-hour OGG tracks per speaker add up and should eventually be cleaned up after a transcript is generated and verified. **For now, while the recording/transcription pipeline is still being debugged, `bot.py` keeps `raw_audio/*.ogg` and the per-speaker debug transcripts in `transcripts/` — nothing is deleted.** Re-add cleanup (auto after N days, or a `--keep-audio` override) once the pipeline is trusted.
- **Type hints throughout:** Coming from a .NET background, prefer explicit type hints on function signatures over relying on inference — makes the codebase easier to navigate for contributors from typed-language backgrounds.
- **Privacy/consent note:** Recording voice chats has all-party-consent implications in some jurisdictions. The README must carry a clear disclaimer; the bot should also announce itself (e.g. a join message) when it starts recording.

## Open Source Notes

- License: MIT (confirmed) — see `LICENSE` file
- `config.example.yml` must never contain real Discord IDs or tokens
- README should include: setup instructions, consent/privacy disclaimer, supported Whisper model sizes and their speed/accuracy tradeoffs, and a note on language support (Whisper supports ~99 languages; set `language` in config to skip auto-detection if the table is consistently one language)

## Known Gotchas

- `openai-whisper` pulls in PyTorch, and pip's default wheel often includes CUDA libraries even on CPU-only machines, causing a large unwanted download. `faster-whisper` avoids this entirely (no PyTorch dependency), which is the main reason it's the chosen backend here.
- Discord voice-receive is not part of vanilla `discord.py`; must use `py-cord` or `discord-ext-voice-recv`.
- Summarization requires the Ollama daemon running locally (`ollama serve`) with the configured model already pulled (`ollama pull <model>`); `ollama_client.py` does not auto-pull.
- **Discord's DAVE (End-to-End Encryption) protocol for voice channels can break voice reception entirely.** py-cord 2.7+ ships a `davey` package (installed via `py-cord[voice]`) that implements DAVE decryption, and `VoiceClient.start_recording`/`stop_recording` still emit a `RuntimeWarning` that reception "may not work as expected." **Confirmed working** against a live voice channel on 2026-07-10 (dev build `2.8.1.dev57+g05cf65fa6`) — real speech was recorded and transcribed. Still worth a quick sanity test after any py-cord upgrade. Track upstream status: https://github.com/Pycord-Development/pycord/issues/3139
- Whether `OGGSink.cleanup()` runs automatically when recording stops **depends on the py-cord build**: the PyPI `2.8.0` release has it as dead/commented code in `AudioReader._stop`, but a newer dev snapshot (`2.8.1.dev57+g05cf65fa6`) calls it automatically — calling it again then raises `SinkException("already finished writing")`. `bot.py` calls `session.sink.cleanup()` explicitly and swallows that specific exception so it works either way.
- `discord.Option(...)` slash-command params only ack the interaction on `ctx.respond()`; if real async work (e.g. `channel.connect()`) happens first, Discord's 3-second interaction timeout can expire and `ctx.respond()` raises `NotFound: Unknown interaction`. Call `await ctx.defer()` as the very first line of any command that does non-trivial work before its first response.
- `sink.audio_data` is keyed by `discord.User | discord.Member` in newer py-cord builds, not a plain `int` (though it may be a raw id in others) — `recorder/sink.py` normalizes with `getattr(user, "id", user)`. Don't assume the key is always an int.
- `transcribe()` (faster-whisper) and `summarize()` (Ollama HTTP call) are both synchronous/blocking and can each run for many seconds to minutes. Calling them directly from a slash-command coroutine blocks the asyncio event loop, which starves Discord's gateway heartbeat — confirmed in testing (`Shard ID None heartbeat blocked for more than 20 seconds`), which risks Discord killing the connection on a real multi-hour session. `bot.py` wraps every call to `transcribe`, `summarize`, and `sink.cleanup` (also blocking, runs `ffmpeg`) in `asyncio.to_thread(...)`.

## Commands / Workflow

- `/record start [name]` — bot joins the caller's current voice channel and begins per-speaker recording. `name` is optional (e.g. `"Session 10"`); defaults to a timestamp if omitted. Warns in the text channel if any speaker present isn't mapped in `config.yml` yet. Sets bot presence to 🔴 "Recording session" (status: dnd).
- `/record stop` — bot stops recording, triggers transcription → merge → summarization pipeline, posts the recap + transcript path in the text channel. Sets presence to 📝 "Summarizing session..." (status: idle) while working, then back to idle listening for `/record start` when done.
- `python3 bot.py [-v|--verbose]` — `-v` raises terminal log level from `WARNING` to `INFO`, printing per-step progress (join, transcribe per speaker, merge, summarize, save, cleanup).

## For AI Agents Working on This Repo

- This is a personal side project by a .NET-background software engineer, currently building in Python. Explanations of Python-specific idioms are welcome when they differ meaningfully from C#/.NET conventions, but don't over-explain basic syntax.
- Prioritize working, testable increments over large speculative refactors — this is built piece by piece (voice capture → transcription → merge → summarization) and each piece should be independently testable with sample OGG files before wiring into the next stage.
- When in doubt about a design decision not covered here, ask rather than assume — and update this file once a decision is made.
