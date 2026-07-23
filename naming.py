import re


def slugify(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()


def speaker_name(discord_id: int, member_names: dict[int, str], players: dict[int, dict]) -> str:
    """discord_id must be a resolved id (never None) — recorder.sink.save_audio() asserts this
    before it ever reaches a caller of speaker_name()."""
    player = players.get(discord_id)
    if player:
        return player["character_name"]
    return member_names.get(discord_id, f"Unknown ({discord_id})")


def resolve_user_id(user: object) -> int | None:
    """Normalizes a py-cord sink's speaker key: some builds key audio_data by
    User/Member, others by a raw int id (or None for an unresolved SSRC)."""
    return getattr(user, "id", user)
