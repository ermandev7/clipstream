"""Operaciones de microseries: crear serie, generar guiones de episodio, regenerar escenas."""
from __future__ import annotations

import logging
from pathlib import Path

from app.core import db
from app.core.events import bus
from app.shared.rights import text_guard
from app.stories import catalog, script

log = logging.getLogger(__name__)
PHASES = ["guion", "personajes", "imagenes", "voz", "montaje"]


class Blocked(Exception):
    def __init__(self, finding: text_guard.Finding) -> None:
        super().__init__(finding.message)
        self.finding = finding


def notify(kind: str, **data) -> None:
    bus.publish(kind, **data)


def create_series(template: str, story_id: str | None, custom_title: str, languages: list[str], episodes: int,
                  name: str = "") -> int:
    if template not in catalog.templates():
        raise ValueError("Plantilla desconocida")
    finding = text_guard.check_story(story_id, template, custom_title)
    if not finding.ok:
        raise Blocked(finding)  # política «block»: no se crea nada hasta corregirlo
    story = catalog.get(story_id)
    episodes = max(1, min(episodes, 20))
    bible = script.series_bible(template, story, episodes, custom_title)
    sid = db.insert("series", name=name.strip() or bible["name"], template=template, story_id=story_id,
                    languages=languages or ["es"], synopsis=bible["synopsis"],
                    bible={k: bible[k] for k in ("episodes", "style", "tone")},
                    rights={"story": finding.__dict__}, status="ready")
    for c in bible["characters"]:
        db.insert("characters", series_id=sid, **c)
    notify("series", series_id=sid)
    return sid


def series_detail(series_id: int) -> dict | None:
    s = db.get("series", series_id)
    if not s:
        return None
    s["characters"] = db.query("SELECT * FROM characters WHERE series_id=? ORDER BY id", (series_id,))
    s["episodes"] = db.query("SELECT * FROM episodes WHERE series_id=? ORDER BY number", (series_id,))
    s["template_info"] = catalog.templates().get(s["template"], {})
    s["story"] = catalog.get(s.get("story_id"))
    return s


def episode_detail(episode_id: int) -> dict | None:
    e = db.get("episodes", episode_id)
    if not e:
        return None
    e["scenes"] = db.query("SELECT * FROM scenes WHERE episode_id=? ORDER BY idx", (episode_id,))
    return e


def _set_phase(ep_id: int, phase: str, state: str) -> None:
    ep = db.get("episodes", ep_id)
    phases = dict(ep.get("phases") or {})
    phases[phase] = state
    db.update("episodes", ep_id, phases=phases)


def apply_guard(ep_id: int, series: dict) -> bool:
    """Pasa el guardián por todas las escenas, guarda correcciones y bloqueos. Devuelve si está bloqueado."""
    scenes = db.query("SELECT * FROM scenes WHERE episode_id=? ORDER BY idx", (ep_id,))
    story = catalog.get(series.get("story_id"))
    result = text_guard.check_episode(scenes, series["template"], story)
    blocked_at: dict[int, text_guard.Finding] = {}
    for f in result.findings:
        if not f.ok:
            idx = int(f.where.split()[1]) - 1
            blocked_at.setdefault(idx, f)
    for s in scenes:
        f = blocked_at.get(s["idx"])
        fields = {"narration_es": s["narration_es"]}
        if f:
            fields.update(status="blocked", block_reason=f.message, suggestion=f.suggestion)
        elif s["status"] == "blocked" and not (s.get("block_reason") or "").startswith("Imagen:"):
            fields.update(status="pending", block_reason=None, suggestion=None)  # el bloqueo visual sigue hasta regenerar
        db.update("scenes", s["id"], **fields)
    db.update("episodes", ep_id, rights=result.to_dict(),
              status="blocked" if result.blocked else "script_ready",
              stage="Bloqueado por derechos" if result.blocked else "Guion listo")
    _set_phase(ep_id, "guion", "blocked" if result.blocked else "done")
    return result.blocked


def generate_episode(series_id: int, number: int) -> int:
    series = series_detail(series_id)
    if series is None:
        raise ValueError("Serie no encontrada")
    existing = next((e for e in series["episodes"] if e["number"] == number), None)
    ep_id = existing["id"] if existing else db.insert("episodes", series_id=series_id, number=number,
                                                      phases={p: "pending" for p in PHASES})
    db.update("episodes", ep_id, status="scripting", stage="Escribiendo guion", error=None)
    notify("episode", episode_id=ep_id, series_id=series_id)
    try:
        previous = [{"number": e["number"], "title": e["title"], "summary": e["summary"]}
                    for e in series["episodes"] if e["number"] < number and e["summary"]]
        res = script.episode_script(series, series["characters"], number, previous)
        db.conn().execute("DELETE FROM scenes WHERE episode_id=?", (ep_id,))
        # Guion nuevo: imágenes, voz y montaje anteriores ya no valen.
        db.update("episodes", ep_id, phases={p: "pending" for p in PHASES}, files={})
        for s in res["scenes"]:
            db.insert("scenes", episode_id=ep_id, **s)
        db.update("episodes", ep_id, title=res["title"], summary=res["summary"])
        _set_phase(ep_id, "personajes", "done" if series["characters"] else "pending")
        apply_guard(ep_id, series)
    except Exception as e:  # noqa: BLE001
        log.exception("Guion fallido")
        db.update("episodes", ep_id, status="error", error=str(e)[:400], stage="Error en el guion")
    notify("episode", episode_id=ep_id, series_id=series_id)
    return ep_id


def series_voices(series: dict) -> dict[str, str]:
    from app.stories import voices

    chosen = (series.get("bible") or {}).get("voices") or {}
    return {lang: chosen.get(lang) or voices.DEFAULT_VOICE[lang] for lang in series.get("languages") or ["es"]}


def narrate_episode(ep_id: int) -> None:
    """Fase «voz»: un WAV por escena e idioma, con tiempos por palabra alineados al guion."""
    from app.stories import narration

    ep = db.get("episodes", ep_id)
    series = series_detail(ep["series_id"])
    scenes = db.query("SELECT * FROM scenes WHERE episode_id=? ORDER BY idx", (ep_id,))
    if any(s["status"] == "blocked" for s in scenes):
        raise Blocked(text_guard.Finding("citas", False, "Hay escenas bloqueadas por derechos",
                                         suggestion="Corrígelas o regenéralas antes de generar la voz"))
    _set_phase(ep_id, "voz", "running")
    db.update("episodes", ep_id, status="voicing", stage="Generando voz", error=None)
    notify("episode", episode_id=ep_id, series_id=series["id"])
    out = narration.episode_dir(series["id"], ep["number"]) / "voice"
    try:
        for n, s in enumerate(scenes, 1):
            files = dict(s.get("files") or {})
            voice = {}
            for lang, v in series_voices(series).items():
                text = s.get(f"narration_{lang}") or ""
                if text.strip():
                    voice[lang] = narration.narrate_scene(text, lang, v, out / f"{s['idx']:02d}_{lang}.wav")
            files["voice"] = voice
            dur = round(max(v["duration"] for v in voice.values()) + narration.PAD, 2) if voice else s["duration"]
            db.update("scenes", s["id"], files=files, duration=dur)
            db.update("episodes", ep_id, stage=f"Voz {n}/{len(scenes)}")
            notify("episode", episode_id=ep_id, series_id=series["id"])
        _set_phase(ep_id, "voz", "done")
        db.update("episodes", ep_id, status="voiced", stage="Voz lista")
    except Exception as e:  # noqa: BLE001
        log.exception("Voz fallida")
        _set_phase(ep_id, "voz", "error")
        db.update("episodes", ep_id, status="error", error=str(e)[:400], stage="Error en la voz")
    notify("episode", episode_id=ep_id, series_id=series["id"])


IMAGE_TRIES = 3  # semillas distintas antes de bloquear una escena por parecido


def _scene_kinds(series: dict, s: dict) -> list[str]:
    by_name = {c["name"]: c for c in series.get("characters") or []}
    return [by_name[n]["kind"] for n in s.get("characters") or [] if by_name.get(n, {}).get("kind")]


def _scene_image(series: dict, ep: dict, s: dict, out_dir: Path, reseed: bool) -> dict:
    """Genera la imagen de una escena con varias semillas si hace falta: primero derechos (parecido con
    personajes protegidos), luego fidelidad (que se vea lo que dice la escena). Se queda la mejor."""
    import shutil

    from app.shared.rights import image_guard
    from app.stories import images

    prev = (s.get("files") or {}).get("image") or {}
    attempt = int(prev.get("attempt", 0)) + (1 if reseed or not prev else 0)
    desc = s["image_prompt"].split(". ")[0][:300]  # primera frase: la acción de la escena
    kinds = _scene_kinds(series, s)
    final = out_dir / f"{s['idx']:02d}.png"
    best: dict | None = None
    tries: list[Path] = []
    for _ in range(IMAGE_TRIES):
        seed = images.seed_for(series["id"], ep["number"], s["idx"], attempt)
        png = images.generate(s["image_prompt"], out_dir / f"{s['idx']:02d}_try{attempt}.png", seed)
        tries.append(png)
        check = image_guard.check_image(png, desc)
        fid = image_guard.check_subject(png, kinds) if check.ok else image_guard.SubjectCheck(False, "", 0.0)
        cand = {"png": png, "seed": seed, "attempt": attempt, "check": check, "fid": fid}
        rank = (check.ok, fid.ok, fid.score)
        if best is None or rank > (best["check"].ok, best["fid"].ok, best["fid"].score):
            best = cand
        if check.ok and fid.ok:
            break
        log.warning("Escena %s semilla %s: %s", s["idx"] + 1, seed, check.message if not check.ok else fid.message)
        attempt += 1
    shutil.copyfile(best["png"], final)
    for t in tries:
        t.unlink(missing_ok=True)
    return {"png": str(final), "seed": best["seed"], "attempt": attempt, "ai_generated": True,
            "check": best["check"].__dict__, "fidelity": best["fid"].__dict__}


def illustrate_episode(ep_id: int, scene_id: int | None = None) -> None:
    """Fase «imágenes»: una imagen por escena (o solo `scene_id`, con otra semilla) + guardián visual."""
    from app.stories import narration

    ep = db.get("episodes", ep_id)
    series = series_detail(ep["series_id"])
    scenes = db.query("SELECT * FROM scenes WHERE episode_id=? ORDER BY idx", (ep_id,))
    targets = [s for s in scenes if scene_id is None or s["id"] == scene_id]
    _set_phase(ep_id, "imagenes", "running")
    db.update("episodes", ep_id, status="illustrating", stage="Generando imágenes", error=None)
    notify("episode", episode_id=ep_id, series_id=series["id"])
    out = narration.episode_dir(series["id"], ep["number"]) / "images"
    try:
        for n, s in enumerate(targets, 1):
            if s["status"] == "blocked" and not (s.get("block_reason") or "").startswith("Imagen:"):
                continue  # bloqueada por el texto: primero se corrige el guion
            files = dict(s.get("files") or {})
            files["image"] = _scene_image(series, ep, s, out, reseed=scene_id is not None)
            chk = files["image"]["check"]
            fields: dict = {"files": files}
            fid = files["image"].get("fidelity") or {"ok": True}
            if not chk["ok"]:
                fields.update(status="blocked", block_reason="Imagen: " + chk["message"], suggestion=chk["suggestion"])
            elif s["status"] == "blocked":
                fields.update(status="review" if not fid["ok"] else "pending", block_reason=None, suggestion=None)
            elif not fid["ok"] and s["status"] != "approved":
                fields.update(status="review")  # calidad, no derechos: se avisa, no se bloquea
            elif fid["ok"] and s["status"] == "review":
                fields.update(status="pending")
            db.update("scenes", s["id"], **fields)
            db.update("episodes", ep_id, stage=f"Imagen {n}/{len(targets)}")
            notify("episode", episode_id=ep_id, series_id=series["id"])
        scenes = db.query("SELECT * FROM scenes WHERE episode_id=? ORDER BY idx", (ep_id,))
        missing = [s for s in scenes if not (s.get("files") or {}).get("image")]
        blocked = [s for s in scenes if s["status"] == "blocked"]
        _set_phase(ep_id, "imagenes", "blocked" if blocked else ("pending" if missing else "done"))
        db.update("episodes", ep_id, status="blocked" if blocked else "illustrated",
                  stage="Bloqueado por derechos" if blocked else "Imágenes listas")
    except Exception as e:  # noqa: BLE001
        log.exception("Imágenes fallidas")
        _set_phase(ep_id, "imagenes", "error")
        db.update("episodes", ep_id, status="error", error=str(e)[:400], stage="Error en las imágenes")
    notify("episode", episode_id=ep_id, series_id=series["id"])


HASHTAGS = {
    "biblicas": {"es": "#historiasbiblicas #biblia #fe #microserie", "en": "#biblestories #bible #faith #series"},
    "mitologia": {"es": "#mitologia #mitosgriegos #leyendas #microserie", "en": "#mythology #greekmyths #legends #series"},
    "fabulas": {"es": "#fabulas #cuentos #moraleja #microserie", "en": "#fables #stories #moral #series"},
    "leyendas": {"es": "#leyendas #misterio #historias #microserie", "en": "#legends #mystery #folklore #series"},
    "frutinovela": {"es": "#frutinovela #telenovela #humor #microserie", "en": "#fruitnovela #drama #funny #series"},
}


def episode_music(series: dict) -> Path | None:
    """La pista elegida para la serie o, si no hay, la base propia del ambiente de la plantilla."""
    from app.shared.music import ambient, library

    chosen = (series.get("bible") or {}).get("music")
    return (library.track_path(chosen) if chosen else None) or \
        library.track_path(ambient.track_id(ambient.TEMPLATE_MOOD.get(series["template"], "solemne")))


def _description(series: dict, ep: dict, lang: str, music: Path | None) -> str:
    from app.shared.rights import licenses

    story = series.get("story") or {}
    head = f"{series['name']} · {'Episodio' if lang == 'es' else 'Episode'} {ep['number']}: {ep.get('title') or ''}"
    lines = [head, "", ep.get("summary") or "", ""]
    if story:
        lines.append(("Basado en: " if lang == "es" else "Based on: ") + f"{story['title']} ({story['source']}, "
                     + ("dominio público)" if lang == "es" else "public domain)"))
    lines.append("Contenido generado con IA." if lang == "es" else "AI-generated content.")
    credit = licenses.credit_line(music) if music else None
    if credit:
        lines.append(("Música: " if lang == "es" else "Music: ") + credit)
    lines += ["", HASHTAGS.get(series["template"], {}).get(lang, "")]
    return "\n".join(lines).strip() + "\n"


def assemble_episode(ep_id: int) -> None:
    """Fase «montaje»: un MP4 1080×1920 por idioma + descripción + informe de derechos."""
    from config.settings import ASSETS_DIR

    from app.shared.rights import licenses
    from app.stories import montage, narration, report

    ep = db.get("episodes", ep_id)
    series = series_detail(ep["series_id"])
    scenes = db.query("SELECT * FROM scenes WHERE episode_id=? ORDER BY idx", (ep_id,))
    if any(s["status"] == "blocked" for s in scenes):
        raise Blocked(text_guard.Finding("citas", False, "Hay escenas bloqueadas por derechos",
                                         suggestion="Corrígelas antes de montar el episodio"))
    missing = [s["idx"] + 1 for s in scenes if not ((s.get("files") or {}).get("image") or {}).get("png")]
    if missing:
        raise ValueError(f"Faltan imágenes en las escenas {missing}")
    langs = [lang for lang in series.get("languages") or ["es"]
             if all(((s.get("files") or {}).get("voice") or {}).get(lang) for s in scenes)]
    if not langs:
        raise ValueError("Falta la voz: genera la voz antes de montar")
    _set_phase(ep_id, "montaje", "running")
    db.update("episodes", ep_id, status="assembling", stage="Montando", error=None)
    notify("episode", episode_id=ep_id, series_id=series["id"])
    folder = narration.episode_dir(series["id"], ep["number"])
    stem = f"ep{ep['number']:02d}"
    try:
        durations = [float(s["duration"]) for s in scenes]
        starts, total = montage.timeline(durations)

        def prog(p: float) -> None:
            db.update("episodes", ep_id, stage=f"Montando vídeo {p:.0%}")
            notify("episode", episode_id=ep_id, series_id=series["id"], progress=round(p * 0.7, 3))

        base = montage.render_base([Path(s["files"]["image"]["png"]) for s in scenes], durations,
                                   folder / "work" / "base.mp4", prog)
        music = episode_music(series)
        whoosh = ASSETS_DIR / "sfx" / "whoosh.wav"
        whoosh = whoosh if whoosh.exists() and licenses.is_allowed(whoosh) else None
        videos: dict[str, str] = {}
        for lang in langs:
            db.update("episodes", ep_id, stage=f"Audio y subtítulos ({lang.upper()})")
            notify("episode", episode_id=ep_id, series_id=series["id"])
            voice = montage.voice_track([Path(s["files"]["voice"][lang]["wav"]) for s in scenes], starts, total,
                                        folder / "work" / f"voice_{lang}.wav")
            ep_title = (f"Episodio {ep['number']}" if lang == "es" else f"Episode {ep['number']}") + \
                (f" · {ep['title']}" if lang == "es" and ep.get("title") else "")
            ass = montage.build_subtitles([s["files"]["voice"][lang]["words"] for s in scenes], starts, total, ep_title, lang)
            out = folder / f"{stem}_{lang}.mp4"
            montage.render_language(base, voice, ass, total, starts, music, whoosh, out)
            (folder / f"{stem}_{lang}.txt").write_text(_description(series, ep, lang, music), encoding="utf-8")
            videos[lang] = str(out)
        rep = report.build(series, db.get("episodes", ep_id), scenes, music, whoosh, videos)
        txt = report.write(rep, folder, stem)
        files = dict(ep.get("files") or {})
        files.update(videos=videos, report=str(txt), duration=total)
        db.update("episodes", ep_id, files=files, status="done", stage="Episodio listo")
        _set_phase(ep_id, "montaje", "done")
    except Exception as e:  # noqa: BLE001
        log.exception("Montaje fallido")
        _set_phase(ep_id, "montaje", "error")
        db.update("episodes", ep_id, status="error", error=str(e)[:400], stage="Error en el montaje")
    notify("episode", episode_id=ep_id, series_id=series["id"])


def produce_episode(series_id: int, number: int) -> int | None:
    """Todo seguido: guion -> imágenes -> voz -> montaje. Se detiene ante un bloqueo o un error
    (el usuario corrige la escena y vuelve a pulsar: se reanuda donde se quedó)."""

    def ep_now() -> dict | None:
        row = db.query("SELECT id FROM episodes WHERE series_id=? AND number=?", (series_id, number))
        return episode_detail(row[0]["id"]) if row else None

    e = ep_now()
    if not e or not e["scenes"] or e["status"] == "error" and (e.get("phases") or {}).get("guion") != "done":
        generate_episode(series_id, number)
        e = ep_now()
    if not e or e["status"] in ("blocked", "error"):
        if e and e["status"] == "blocked":
            db.update("episodes", e["id"], stage="Bloqueado por derechos")
            notify("episode", episode_id=e["id"], series_id=series_id)
        return e["id"] if e else None
    if any(not ((s.get("files") or {}).get("image") or {}).get("png") for s in e["scenes"]):
        illustrate_episode(e["id"])
        e = ep_now()
        if e["status"] in ("blocked", "error"):
            return e["id"]
    langs = (series_detail(series_id) or {}).get("languages") or ["es"]
    if any(not ((s.get("files") or {}).get("voice") or {}).get(lang) for s in e["scenes"] for lang in langs
           if s.get(f"narration_{lang}")):
        narrate_episode(e["id"])
        e = ep_now()
        if e["status"] == "error":
            return e["id"]
    assemble_episode(e["id"])
    return e["id"]


REWRITE_PROMPT = """Reescribe esta escena de la microserie «{name}» para resolver un problema de derechos.
Problema: {reason}
Solución propuesta: {suggestion}
Escena anterior: {prev}
Escena a reescribir: {text}
Escena siguiente: {next}
{quote_rule}
JSON: {{"narration_es": "...", {en_field}"subject": "in English, max 15 words: who does what, using what they are, never names",
 "setting": "in English, place and light"}}"""


def rewrite_scene(scene_id: int) -> None:
    from app.shared.llm import ollama

    sc = db.get("scenes", scene_id)
    ep = db.get("episodes", sc["episode_id"])
    series = series_detail(ep["series_id"])
    scenes = db.query("SELECT * FROM scenes WHERE episode_id=? ORDER BY idx", (ep["id"],))
    prev = next((s["narration_es"] for s in scenes if s["idx"] == sc["idx"] - 1), "(inicio)")
    nxt = next((s["narration_es"] for s in scenes if s["idx"] == sc["idx"] + 1), "(fin)")
    tpl = catalog.templates()[series["template"]]
    db.update("scenes", scene_id, status="generating")
    notify("episode", episode_id=ep["id"], series_id=series["id"])
    res = ollama.generate_json(script.SYSTEM, REWRITE_PROMPT.format(
        name=series["name"], reason=sc.get("block_reason") or "mejorar la escena", suggestion=sc.get("suggestion") or "",
        prev=prev, text=sc["narration_es"], next=nxt, quote_rule=tpl.get("quote_rule", ""),
        en_field='"narration_en": "...", ' if "en" in (series.get("languages") or []) else ""), temperature=0.5)
    text = str(res.get("narration_es") or sc["narration_es"]).strip()
    prompt = sc["image_prompt"]
    if res.get("subject"):
        subject, setting = script.to_english([str(res["subject"]), str(res.get("setting") or "")])
        prompt = script.build_image_prompt(subject, sc.get("characters") or [], series["characters"], setting,
                                           (series.get("bible") or {}).get("style") or tpl["style"])
    db.update("scenes", scene_id, narration_es=text, narration_en=str(res.get("narration_en") or sc["narration_en"]),
              image_prompt=prompt, duration=script.estimate_duration(text), status="pending", block_reason=None,
              suggestion=None)
    apply_guard(ep["id"], series)
    notify("episode", episode_id=ep["id"], series_id=series["id"])
