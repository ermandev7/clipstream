from app.shared.rights import text_guard
from app.stories import catalog


def test_catalog_story_is_public_domain():
    f = text_guard.check_story("biblia-jonas", "biblicas")
    assert f.ok and "dominio público" in f.message


def test_frutinovela_is_original():
    assert text_guard.check_story(None, "frutinovela").ok


def test_custom_unknown_story_blocks_without_llm(monkeypatch):
    monkeypatch.setattr(text_guard, "llm_public_domain", lambda t: {"public_domain": False, "confidence": 0.9, "reason": "Novela de 2005"})
    f = text_guard.check_story(None, "leyendas", "Una saga juvenil moderna")
    assert not f.ok and f.suggestion


def test_franchise_detected():
    f = text_guard.check_franchises("Heracles se encontró con Phil de Hércules y cantó De cero a héroe", catalog.get("mito-hercules"), "Escena 1")
    assert not f.ok and "de cero a héroe" in f.message


def test_clean_text_passes():
    assert text_guard.check_franchises("Heracles luchó contra el león de Nemea.", None, "x").ok


def test_episode_fixes_and_blocks():
    story = catalog.get("biblia-creacion")
    scenes = [
        {"idx": 0, "narration_es": "Y Dios dijo: «Sea la luz, y fue la luz».", "image_prompt": "light over dark waters"},
        {"idx": 1, "narration_es": "Entonces Dios dijo: «Os doy este teléfono móvil para que habléis conmigo siempre».",
         "image_prompt": "garden"},
    ]
    r = text_guard.check_episode(scenes, "biblicas", story)
    assert "fué la luz" in scenes[0]["narration_es"]  # corregida a la RV1909
    assert r.blocked and any(f.check == "citas" and not f.ok and f.where == "Escena 2" for f in r.findings)


def test_image_prompt_franchise_blocks():
    r = text_guard.check_episode([{"idx": 0, "narration_es": "El león dormía.", "image_prompt": "Simba sleeping"}],
                                 "fabulas", catalog.get("fabula-leon-raton"))
    assert r.blocked and r.findings[-1].where == "Escena 1 (imagen)"


def test_single_quoted_invented_dialogue_is_blocked():
    story = catalog.get("biblia-jonas")
    scenes = [{"idx": 0, "narration_es": "La voz de un marinero lo despierta. '¿Qué haces, dormilón? ¡Clama a tu Dios!'",
               "image_prompt": "ship"}]
    r = text_guard.check_episode(scenes, "biblicas", story)
    citas = [f for f in r.findings if f.check == "citas"][0]
    # O se corrige al texto exacto de Jonás 1:6, o se bloquea: nunca pasa una cita inventada.
    assert (not citas.ok) or ("tienes, dormilón" in scenes[0]["narration_es"])
