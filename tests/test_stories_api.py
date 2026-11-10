"""API de microseries sin LLM: el guion se simula."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from config.settings import settings

    from app.core import db

    original = settings.storage_dir
    settings.storage_dir = tmp_path_factory.mktemp("storage")
    settings.stories_enabled = True  # desactivadas en la app; aquí se prueban igualmente
    db._local.conn = None
    db._initialized = False
    from app.main import app

    with TestClient(app) as c:
        yield c
    settings.storage_dir = original
    settings.stories_enabled = False
    db._local.conn = None
    db._initialized = False


@pytest.fixture
def fake_llm(monkeypatch):
    from app.stories import script

    monkeypatch.setattr(script, "series_bible", lambda t, s, n, c="": {
        "name": "Jonás", "synopsis": "Un profeta huye.", "style": "oil", "tone": "solemne",
        "characters": [{"name": "Jonás", "role": "protagonista", "description": "profeta", "visual": "young man, brown robe"}],
        "episodes": [{"number": i + 1, "title": f"Ep {i + 1}", "summary": ""} for i in range(n)]})
    monkeypatch.setattr(script, "episode_script", lambda series, chars, number, prev: {
        "title": "El viaje", "summary": "Jonás huye.",
        "scenes": [{"idx": 0, "narration_es": "Jonás huye a Tarsis en un barco.", "narration_en": "", "image_prompt": "ship, oil",
                    "characters": ["Jonás"], "duration": 5.0},
                   {"idx": 1, "narration_es": "El marinero dijo: «Toma este teléfono y llama a tu Dios ahora mismo».",
                    "narration_en": "", "image_prompt": "storm", "characters": [], "duration": 6.0},
                   {"idx": 2, "narration_es": "Un gran pez lo traga.", "narration_en": "", "image_prompt": "Simba the lion",
                    "characters": [], "duration": 4.0}]})


def test_catalog(client):
    d = client.get("/api/stories/catalog").json()
    assert "biblicas" in d["templates"] and len(d["stories"]) >= 30


def test_non_public_domain_story_is_blocked(client, monkeypatch):
    from app.shared.rights import text_guard

    monkeypatch.setattr(text_guard, "llm_public_domain", lambda t: {"public_domain": False, "confidence": 0.95, "reason": "autora viva"})
    r = client.post("/api/stories/series", json={"template": "leyendas", "custom_title": "Saga moderna", "episodes": 2})
    assert r.status_code == 422 and r.json()["detail"]["blocked"]


def test_series_episode_and_guard(client, fake_llm):
    s = client.post("/api/stories/series", json={"template": "biblicas", "story_id": "biblia-jonas", "episodes": 3}).json()
    assert s["name"] == "Jonás" and len(s["characters"]) == 1
    from app.stories import service

    ep_id = service.generate_episode(s["id"], 1)  # síncrono en la prueba
    e = client.get(f"/api/stories/episodes/{ep_id}").json()
    assert e["status"] == "blocked"
    st = {sc["idx"]: sc["status"] for sc in e["scenes"]}
    assert st[0] == "pending" and st[1] == "blocked" and st[2] == "blocked"  # cita inventada + personaje protegido
    blocked = e["scenes"][1]
    assert client.patch(f"/api/stories/scenes/{blocked['id']}", json={"status": "approved"}).status_code == 409
    # Corregir el texto desbloquea esa escena (el guardián vuelve a revisar).
    e = client.patch(f"/api/stories/scenes/{blocked['id']}", json={"narration_es": "El marinero le pidió que rezara."}).json()
    assert {sc["idx"]: sc["status"] for sc in e["scenes"]}[1] == "pending"
    e = client.patch(f"/api/stories/scenes/{e['scenes'][2]['id']}", json={"image_prompt": "a huge whale under the sea, oil"}).json()
    assert e["status"] == "script_ready"
    assert client.post(f"/api/stories/series/{s['id']}/episodes/9").status_code == 400


def test_produce_stops_at_block_and_new_script_resets_phases(client, fake_llm, monkeypatch):
    from app.core import db
    from app.stories import service

    s = client.post("/api/stories/series", json={"template": "biblicas", "story_id": "biblia-jonas", "episodes": 2}).json()
    called = []
    monkeypatch.setattr(service, "illustrate_episode", lambda *a, **k: called.append("img"))
    ep_id = service.produce_episode(s["id"], 1)
    e = service.episode_detail(ep_id)
    assert e["status"] == "blocked" and e["stage"] == "Bloqueado por derechos" and not called
    # Desde la API también se rechaza mientras haya escenas bloqueadas (o faltan modelos): nunca 200.
    assert client.post(f"/api/stories/series/{s['id']}/episodes/1/produce").status_code == 409
    db.update("episodes", ep_id, phases={p: "done" for p in service.PHASES}, files={"videos": {"es": "x.mp4"}})
    service.generate_episode(s["id"], 1)
    e = service.episode_detail(ep_id)
    assert e["phases"]["montaje"] == "pending" and not (e["files"] or {}).get("videos")
