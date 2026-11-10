"""Fidelidad de las imágenes: reparto, nombres, prompt y elección de la mejor semilla (sin GPU)."""
from app.stories import script

CHARS = [
    {"name": "El león", "kind": "lion", "visual": "golden mane, amber eyes, scar on the nose"},
    {"name": "El ratón", "kind": "mouse", "visual": "tiny, grey fur, pink ears"},
]


def test_fable_cast_drops_invented_characters():
    chars = CHARS + [{"name": "El zorro", "kind": "fox", "visual": "red fur", "role": "secundario"},
                     {"name": "La golondrina", "kind": "swallow", "visual": "blue feathers", "role": "secundario"}]
    assert [c["name"] for c in script.keep_source_cast(chars, ["León", "Ratón"])] == ["El león", "El ratón"]


def test_scene_names_match_bible_and_cap_at_two():
    assert script.match_characters(["León", "Rato", "Zorro"], CHARS) == ["El león", "El ratón"]
    assert script.match_characters(["ratón"], CHARS) == ["El ratón"]


def test_prompt_puts_subject_first_and_kind_in_every_look():
    p = script.build_image_prompt("A lion laughs at a tiny mouse", ["El león", "El ratón"], CHARS,
                                  "savanna at dawn", "storybook watercolor")
    assert p.startswith("A lion laughs at a tiny mouse. lion, golden mane")
    assert "mouse, tiny, grey fur" in p and "León" not in p and p.endswith("no text, no watermark")


def test_kind_is_cleaned():
    assert script.clean_kind("Young MAN (prophet)!") == "young man prophet"


def test_best_try_is_kept(tmp_path, monkeypatch):
    from app.shared.rights import image_guard
    from app.stories import images, service

    calls = []

    def gen(prompt, out, seed):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(str(seed).encode())
        calls.append((out, seed))
        return out

    scores = iter([0.1, 0.3, 0.2])  # ninguna llega al 40 %: se queda la de 0.3
    monkeypatch.setattr(images, "generate", gen)
    monkeypatch.setattr(image_guard, "check_image", lambda p, d: image_guard.ImageCheck(True, "ok"))
    monkeypatch.setattr(image_guard, "check_subject",
                        lambda p, k: (lambda sc: image_guard.SubjectCheck(False, "parece fox", sc, "fox"))(next(scores)))
    series = {"id": 1, "characters": CHARS}
    scene = {"idx": 0, "image_prompt": "A lion. lion", "characters": ["El león"], "files": {}}
    r = service._scene_image(series, {"number": 1}, scene, tmp_path, reseed=False)
    assert r["fidelity"]["score"] == 0.3 and not r["fidelity"]["ok"]
    assert (tmp_path / "00.png").read_bytes() == str(calls[1][1]).encode() and r["seed"] == calls[1][1]
    assert not any(p.exists() for p, _ in calls)  # los intentos se borran
    assert sorted(x.name for x in tmp_path.iterdir()) == ["00.png"]


def test_spanish_detection_and_translation_batch(monkeypatch):
    from app.shared.llm import ollama

    sent = {}

    def fake(system, prompt, temperature=0.3, num_ctx=4096):
        sent["prompt"] = prompt
        return {"lines": ["the lion mocks the mouse", "forest at dusk"]}

    monkeypatch.setattr(ollama, "generate_json", fake)
    out = script.to_english(["el león se burla del ratón", "A ship in a storm", "el bosque, al atardecer"])
    assert out == ["the lion mocks the mouse", "A ship in a storm", "forest at dusk"]
    assert "A ship" not in sent["prompt"]  # solo se traduce lo que está en español
    assert not script.looks_spanish("A lion laughs at a tiny mouse in the savanna")
