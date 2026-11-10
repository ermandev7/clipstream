"""Fase de imágenes sin GPU: SDXL y CLIP se simulan."""
import pytest

from app.shared.rights import image_guard
from app.stories import images, prompt_embed


class FakeTok:
    def __call__(self, text, add_special_tokens=False):
        class R:
            input_ids = list(range(len(text.split())))
        return R()

    def decode(self, ids):
        return " ".join(f"w{i}" for i in ids)


def test_long_prompt_is_chunked_not_truncated():
    parts = prompt_embed.chunks(FakeTok(), " ".join(["x"] * 160))
    assert len(parts) == 3 and len(parts[0].split()) == 75 and len(parts[2].split()) == 10


def test_short_prompt_single_chunk():
    assert len(prompt_embed.chunks(FakeTok(), "a ship in a storm")) == 1


def test_seed_is_stable_and_changes_with_attempt():
    assert images.seed_for(1, 1, 0, 0) == images.seed_for(1, 1, 0, 0) != images.seed_for(1, 1, 0, 1)


@pytest.fixture
def episode(tmp_path, monkeypatch):
    from config.settings import settings

    from app.core import db
    from app.stories import service

    monkeypatch.setattr(settings, "storage_dir", tmp_path)
    db._local.conn = None
    db._initialized = False
    sid = db.insert("series", name="Jonás", template="biblicas", story_id="biblia-jonas", languages=["es"], bible={})
    ep = db.insert("episodes", series_id=sid, number=1, phases={p: "pending" for p in service.PHASES})
    for i, p in enumerate(["a prophet on a ship", "a cute cartoon lion cub", "a whale"]):
        db.insert("scenes", episode_id=ep, idx=i, narration_es="texto", image_prompt=p, status="pending")
    yield ep
    db._local.conn = None
    db._initialized = False


def test_illustrate_blocks_lookalike_after_retries(episode, monkeypatch):
    from app.core import db
    from app.stories import service

    seeds = []

    def fake_generate(prompt, out, seed):
        seeds.append((prompt, seed))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"png")
        return out

    def fake_check(path, desc):
        if "cartoon" in desc:
            return image_guard.ImageCheck(False, "La imagen se parece a «Simba» (80%)", "Simba", 0.8, "Regenera")
        return image_guard.ImageCheck(True, "ok")

    monkeypatch.setattr(images, "generate", fake_generate)
    monkeypatch.setattr(image_guard, "check_image", fake_check)
    service.illustrate_episode(episode)
    e = service.episode_detail(episode)
    st = [s["status"] for s in e["scenes"]]
    assert st == ["pending", "blocked", "pending"] and e["status"] == "blocked"
    assert e["scenes"][1]["block_reason"].startswith("Imagen:")
    assert len([1 for p, _ in seeds if "cartoon" in p]) == service.IMAGE_TRIES  # probó varias semillas
    assert e["scenes"][0]["files"]["image"]["ai_generated"] and e["phases"]["imagenes"] == "blocked"
    # El bloqueo visual no lo levanta el guardián de texto, solo una imagen nueva que pase.
    service.apply_guard(episode, service.series_detail(e["series_id"]))
    assert db.get("scenes", e["scenes"][1]["id"])["status"] == "blocked"
    db.update("scenes", e["scenes"][1]["id"], image_prompt="a lion cub, oil painting")
    monkeypatch.setattr(image_guard, "check_image", lambda p, d: image_guard.ImageCheck(True, "ok"))
    service.illustrate_episode(episode, e["scenes"][1]["id"])
    e = service.episode_detail(episode)
    assert e["scenes"][1]["status"] == "pending" and e["status"] == "illustrated"
