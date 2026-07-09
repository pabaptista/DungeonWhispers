# DungeonWhispers

A self-hosted Discord bot that joins a voice channel during a tabletop RPG session, records each speaker separately, transcribes the audio, and produces an AI-generated session summary (recap of decisions, combat, loot, NPCs, cliffhangers).

Built for D&D tables, but generic enough for any voice-channel meeting/session that needs a transcript + summary.

## Status

Early-stage side project. Architecture is decided; implementation is in progress. Treat this file as the source of truth for design decisions — update it when decisions change, don't let it drift from the code.

## Tech Stack

- **Language:** Python 3.11+
- **Discord library:** `py-cord` (fork of discord.py with voice-receive support via `discord.sinks`)
- **Audio format:** OGG/Opus (`discord.sinks.OGGSink`) — Discord's voice data is already Opus-encoded, so we save directly to OGG rather than decoding to WAV, avoiding both a conversion step and the storage bloat of raw WAV over multi-hour sessions.
- **Transcription:** `faster-whisper` (CTranslate2-based, not PyTorch) — avoids the CUDA/CPU torch wheel confusion of the original `openai-whisper` package, and is faster on CPU for long sessions.
- **Summarization:** Local LLM via [Ollama](https://ollama.com) (default model `gemma-4-E4B`, quantized GGUF) — no external API calls, entire pipeline (voice capture → transcription → summarization) runs fully offline/self-hosted.
- **Config:** YAML (`config.yml`, gitignored; `config.example.yml` committed)

## Architecture (high level)

```
Discord Voice Channel
  → py-cord voice listener (per-user audio sink, OGG/Opus)
  → raw_audio/{user_id}.ogg per speaker
  → faster-whisper transcription per track (timestamped segments)
  → merge all speakers' segments into one chronological timeline
  → tag each line with character name via config.yml (Discord ID → player → character)
  → local Ollama summarization (D&D-recap system prompt)
  → output: posted to a Discord text channel + saved as markdown file
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
├── raw_audio/              # Temp OGG files, deleted after transcription (gitignored)
├── transcripts/            # Output markdown transcripts + summaries
├── requirements.txt
├── README.md
└── CLAUDE.md / AGENTS.md   # This file
```

## Conventions

- **Config over hardcoding:** Discord IDs, character name mappings, model sizes, and prompts live in `config.yml`, never hardcoded in source.
- **No secrets in git:** Discord bot token and `config.yml` are gitignored. Only `config.example.yml` (with placeholder values) is committed.
- **Pluggable transcription backend:** Keep `transcription/whisper_backend.py` behind a simple interface (e.g. `transcribe(audio_path, language=None) -> list[Segment]`) so a different backend (API-based, different local model) can be swapped in without touching the rest of the pipeline.
- **Delete raw audio after processing:** Multi-hour OGG tracks per speaker add up; once a transcript is generated and verified, raw audio in `raw_audio/` should be cleaned up (either automatically after N days, or via a `--keep-audio` flag override).
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

## Commands / Workflow (once implemented)

- `/record start` — bot joins the caller's current voice channel and begins per-speaker recording
- `/record stop` — bot stops recording, triggers transcription → merge → summarization pipeline, posts result

## For AI Agents Working on This Repo

- This is a personal side project by a .NET-background software engineer, currently building in Python. Explanations of Python-specific idioms are welcome when they differ meaningfully from C#/.NET conventions, but don't over-explain basic syntax.
- Prioritize working, testable increments over large speculative refactors — this is built piece by piece (voice capture → transcription → merge → summarization) and each piece should be independently testable with sample OGG files before wiring into the next stage.
- When in doubt about a design decision not covered here, ask rather than assume — and update this file once a decision is made.
