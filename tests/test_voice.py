from hearthsmith.voice import chunk, seconds_for


def test_short_nag_is_one_chunk():
    assert chunk("Ledger's clean. Nothing to hammer on.", 2.6, 14.0) == \
        ["Ledger's clean. Nothing to hammer on."]


def test_long_answer_splits_on_sentences_within_budget():
    text = " ".join(f"Sentence number {i} has exactly six words." for i in range(12))
    parts = chunk(text, 2.6, 14.0)
    assert len(parts) > 1 and " ".join(parts) == text
    assert all(len(p.split()) <= int(14.0 * 2.6) for p in parts)
    assert all(p.endswith(".") for p in parts)  # cuts land between sentences, not inside


def test_seconds_track_words_and_clamp():
    assert seconds_for("Oi.", 2.6, 14.0) == 1.5
    assert seconds_for(" ".join(["word"] * 16), 2.6, 14.0) == 6.8
    assert seconds_for(" ".join(["word"] * 200), 2.6, 14.0) == 14.0
