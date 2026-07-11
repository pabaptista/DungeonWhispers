from naming import slugify, speaker_name


def test_slugify_lowercases_and_hyphenates():
    assert slugify("Session 10") == "session-10"


def test_slugify_strips_leading_trailing_punctuation():
    assert slugify("  !!Eris Thornveil!!  ") == "eris-thornveil"


def test_slugify_collapses_runs_of_punctuation():
    assert slugify("a---b__c") == "a-b-c"


def test_slugify_empty_string():
    assert slugify("") == ""


def test_speaker_name_uses_character_name_when_mapped():
    players = {123: {"character_name": "Eris Thornveil", "player_name": "Pedro"}}
    assert speaker_name(123, {}, players) == "Eris Thornveil"


def test_speaker_name_falls_back_to_display_name_when_unmapped():
    players = {}
    member_names = {456: "SomePlayer"}
    assert speaker_name(456, member_names, players) == "SomePlayer"


def test_speaker_name_falls_back_to_unknown_id_when_fully_unmapped():
    assert speaker_name(789, {}, {}) == "Unknown (789)"


def test_speaker_name_prefers_players_mapping_over_display_name():
    players = {123: {"character_name": "Eris Thornveil"}}
    member_names = {123: "SomePlayer"}
    assert speaker_name(123, member_names, players) == "Eris Thornveil"
