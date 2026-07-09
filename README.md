# DungeonWhispers

A self-hosted Discord bot that joins a voice channel during a tabletop RPG session, records each speaker separately, transcribes the audio, and produces an AI-generated session summary (recap of decisions, combat, loot, NPCs, cliffhangers).

Runs fully offline/self-hosted: local transcription (`faster-whisper`) + local summarization (`Ollama`). No audio or transcript ever leaves your machine.

## ⚠️ Privacy / consent

Recording voice chats requires **all-party consent** in some jurisdictions. Make sure everyone at the table is OK with being recorded before you run `/record start`. The bot announces itself in the voice/text channel when a recording begins — don't disable that.

## Status

Early-stage. `/record start` → `/record stop` has been confirmed working end-to-end against a live Discord voice channel. See [CLAUDE.md](CLAUDE.md#known-gotchas) for py-cord version quirks around voice encryption and sink cleanup.

See [CLAUDE.md](CLAUDE.md) for the full architecture and design decisions.

## Requirements

- Python 3.11+
- **`ffmpeg`** on `PATH` (used to encode recorded audio to OGG — not a pip package, install via your OS package manager)
- [Ollama](https://ollama.com) installed, running (`ollama serve`), with a model pulled (e.g. `ollama pull gemma4-unsloth-nothink:latest` or any other local chat model)
- A Discord bot token with voice + message permissions, invited to your server with the `applications.commands` and `bot` scopes

## Setup

```bash
git clone <this repo>
cd DungeonWhispers
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yml config.yml
```

Edit `config.yml`:

- `discord.bot_token` — your bot's token
- `ollama.host` / `ollama.model` — where Ollama is running and which model to use
- `whisper.*` — model size, device, compute type, language
- `players` — map Discord IDs to player/character names

`config.yml` is gitignored — never commit it.

## Running the bot

```bash
python3 bot.py            # quiet
python3 bot.py --verbose  # print progress (join, transcribe, summarize, ...) to the terminal
```

In a voice channel: `/record start [name]` to begin (e.g. `/record start name:"Session 10"` — optional, defaults to a timestamp), `/record stop` to end. The bot transcribes each speaker, merges them into one timeline, summarizes it with your local Ollama model, posts the recap in the text channel, and saves the merged transcript to `transcripts/`.

Raw audio (`raw_audio/`) and each speaker's individual transcript are currently kept, not deleted, to make debugging easier — both are gitignored.

## Trying individual pieces

```bash
# transcription: transcribes test.ogg using config.yml's whisper settings
python3 -m transcription.whisper_backend

# summarization: sends a sample transcript line to your local Ollama model
python3 -m summarization.ollama_client
```

## Whisper model sizes

`faster-whisper` model size is set via `whisper.model_size` in `config.yml`. Bigger = more accurate, slower, more RAM:

| Size                    | Relative speed | Accuracy       | Notes                                          |
| ----------------------- | -------------- | -------------- | ---------------------------------------------- |
| `tiny`                  | fastest        | lowest         | quick tests only                               |
| `base`                  | very fast      | low            | still rough                                    |
| `small`                 | fast           | decent         | usable on CPU for long sessions                |
| `medium`                | moderate       | good           | slower on CPU                                  |
| `large-v2` / `large-v3` | slow           | best           | needs a decent CPU/GPU for multi-hour sessions |
| `turbo`                 | fast           | close to large | good speed/accuracy tradeoff for CPU           |

## Language support

Whisper auto-detects among ~99 languages by default. If your table always speaks the same language, set `whisper.language` in `config.yml` (e.g. `"en"`, `"pt"`) to skip auto-detection and speed things up slightly.

## License

MIT — see [LICENSE](LICENSE).
