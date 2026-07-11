import re


def slugify(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()


def speaker_name(discord_id: int, member_names: dict[int, str], players: dict[int, dict]) -> str:
    player = players.get(discord_id)
    if player:
        return player["character_name"]
    return member_names.get(discord_id, f"Unknown ({discord_id})")
