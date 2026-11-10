"""Pipeline del clipper para vídeo publicado y archivo local, y render de clips."""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

import numpy as np

from app.clipper import common
from app.clipper.detection import boundaries, dedup, llm_judge, scorer
from app.clipper.ingest.local_file import open_local
from app.clipper.ingest.vod import download_vod
from app.clipper.signals.chat import youtube_chat
from app.core import db
from app.core.events import clip_changed, log_event, source_changed
from app.shared.metadata.writer import write_txt
from app.shared.render import ffmpeg
from app.shared.render.vertical import RenderSpec, render
from app.shared.subtitles.ass import build_ass, build_srt, style_by_name

log = logging.getLogger(__name__)

# Rango de progreso de cada fase del análisis (0-1).
STAGES = {"Descargando": (0.0, 0.25), "Preparando audio": (0.25, 0.3), "Transcribiendo": (0.3, 0.7),
          "Detectando": (0.7, 0.75), "Evaluando con IA": (0.75, 1.0)}


class Progress:
    def __init__(self, source_id: int) -> None:
        self.sid = source_id
        self._last = 0.0

    def __call__(self, stage: str, frac: float = 0.0, status: str | None = None) -> None:
        lo, hi = STAGES.get(stage, (0.0, 1.0))
        p = round(lo + (hi - lo) * max(0.0, min(1.0, frac)), 3)
        now = time.monotonic()
        if frac in (0.0, 1.0) or now - self._last > 0.5:
            self._last = now
            fields = {"progress": p, "stage": stage, "stage_progress": round(max(0.0, min(1.0, frac)), 3)}
            if status:
                fields["status"] = status
            db.update("sources", self.sid, **fields)
            source_changed(self.sid)


def analyze_media(source_id: int) -> None:
    src = db.get("sources", source_id)
    if src is None:
        return
    prog = Progress(source_id)
    workdir = common.source_dir(source_id)
    try:
        # 1. INGESTA
        if src["kind"] == "vod":
            prog("Descargando", 0, status="downloading")
            log_event(source_id, f"Descargando {src['url']}")
            if src.get("platform") == "kick":
                from app.clipper.ingest.kick import download_vod as kick_vod

                media = kick_vod(src["url"], workdir, lambda f, _m: prog("Descargando", f))
            else:
                media = download_vod(src["url"], workdir, lambda f, _m: prog("Descargando", f))
            db.update("sources", source_id, path=str(media.path), title=media.title, channel=media.channel,
                      duration=media.duration, was_live=int(media.was_live))
            log_event(source_id, f"Descargado: {media.title} ({media.duration / 60:.1f} min)")
        else:
            media = open_local(src["path"])
            db.update("sources", source_id, title=media.title, duration=media.duration)
            log_event(source_id, f"Archivo local: {media.path.name} ({media.duration / 60:.1f} min)")
        src = db.get("sources", source_id)
        duration = media.duration
        n = int(duration)

        # 2. SEÑALES
        prog("Preparando audio", 0, status="analyzing")
        wav = ffmpeg.extract_audio(media.path, workdir / "audio.wav")
        audio_db = ffmpeg.audio_levels(wav)
        from app.clipper.signals.audio_events import classify_wav

        events = classify_wav(wav)
        common.save_events(source_id, events)
        prog("Preparando audio", 1)

        chat_result: dict = {}
        chat_thread = None
        if src["kind"] == "vod" and media.was_live and src.get("platform") != "kick":
            log_event(source_id, "Era un directo: descargando la repetición del chat")

            def _chat() -> None:
                chat_result["series"] = youtube_chat.replay_series(src["url"], n)

            chat_thread = threading.Thread(target=_chat, daemon=True)
            chat_thread.start()

        t0 = time.perf_counter()
        prog("Transcribiendo", 0)
        from app.shared.transcription import whisper  # import tardío: carga CUDA solo si hace falta

        transcript = whisper.transcribe(wav, language=None, progress=lambda f: prog("Transcribiendo", f))
        common.save_transcript(source_id, transcript)
        log_event(source_id, f"Transcripción: {len(transcript.words)} palabras ({transcript.language}) en {time.perf_counter() - t0:.0f} s")

        chat_rate = chat_hits = None
        if chat_thread:
            chat_thread.join(timeout=300)
            series = chat_result.get("series")
            if series:
                chat_rate, chat_hits = series["rate"], series["hits"]
                for k in ("donation", "sub"):
                    events[k] = series[k]
                db.update("sources", source_id, has_chat=1)
                log_event(source_id, f"Chat: {int(chat_rate.sum())} mensajes, {int(series['donation'].sum())} "
                                     f"donaciones, {int(series['sub'].sum())} suscripciones")
                common.save_events(source_id, events)
            else:
                log_event(source_id, "Sin repetición de chat disponible", "warn")

        # 3. DETECCIÓN
        prog("Detectando", 0)
        sig = scorer.SignalSet(duration_s=n, audio_db=audio_db[:n] if len(audio_db) >= n else np.pad(audio_db, (0, n - len(audio_db)), mode="edge"),
                               chat_rate=chat_rate, chat_hits=chat_hits, transcript=transcript, events=events)
        curve, parts = scorer.combined_curve(sig)
        common.save_signals(source_id, duration, curve, sig.audio_db, chat_rate, laughter=events.get("laughter"))
        source_changed(source_id)
        target = scorer.target_clip_count(duration)
        # En vídeo completo se comparan entre sí: umbral bajo, nos quedamos con los mejores.
        cands = scorer.candidates_from_curve(curve, parts, max_candidates=target * 2 + 2, min_height=0.0,
                                             chat_rate=chat_rate)
        cands = [boundaries.adjust(c, transcript, duration) for c in cands]
        cands = scorer.penalize_music_only(cands, events, transcript)
        cands = dedup.merge(cands)
        log_event(source_id, f"{len(cands)} candidatos tras señales")
        prog("Detectando", 1)

        # 4. JUEZ LLM
        for i, c in enumerate(cands):
            prog("Evaluando con IA", 0.85 * i / max(1, len(cands)))
            llm_judge.judge(c, transcript, src["title"] or "")
            if c.llm_score is not None:
                log_event(source_id, f"IA {c.start / 60:.0f}:{c.start % 60:02.0f} → {c.llm_score:.0f}/100 · {c.llm_reason}")
        best = dedup.select_diverse(dedup.drop_weak(cands), target)

        from app.core.gpu import gpu
        from app.workers.tasks import render_clip_task

        # Metadatos ES/EN con el LLM aún cargado; después se libera la VRAM.
        metas = []
        for i, c in enumerate(best):
            prog("Evaluando con IA", 0.85 + 0.15 * i / max(1, len(best)))
            metas.append(common.clip_metadata(c, transcript, src["title"] or ""))
        gpu.release()
        prog("Evaluando con IA", 1)
        stored = []
        for c, meta in zip(best, metas):
            cid = common.store_clip(src, c, transcript, meta=meta)
            clip_changed(cid)
            stored.append((c.start, cid))
        db.update("sources", source_id, status="rendering", progress=1.0, stage="Generando clips", stage_progress=0)
        for _, cid in sorted(stored):  # uno a uno, en el orden del vídeo
            render_clip_task(cid)
        source_changed(source_id)
        log_event(source_id, f"{len(best)} clips seleccionados; renderizando")
    except Exception as e:  # noqa: BLE001
        log.exception("Análisis fallido")
        db.update("sources", source_id, status="error", error=str(e)[:500])
        source_changed(source_id)
        log_event(source_id, f"Error: {e}", "error")


# ---- Cola de render: un clip cada vez, en orden ------------------------------------------------
# Huey tiene varios hilos; sin esto se renderizaban 3 clips a la vez y el progreso era confuso.
# Cada render espera su turno: primero la fuente más antigua y, dentro de ella, el clip que antes aparece.
_render_cv = threading.Condition()
_render_waiting: dict[int, tuple] = {}
_render_busy = False


def _render_turn(clip: dict) -> None:
    global _render_busy
    key = (clip["source_id"], clip["start"], clip["id"])
    with _render_cv:
        _render_waiting[clip["id"]] = key
        _render_cv.wait_for(lambda: not _render_busy and min(_render_waiting.values()) == key)
        del _render_waiting[clip["id"]]
        _render_busy = True


def _render_done() -> None:
    global _render_busy
    with _render_cv:
        _render_busy = False
        _render_cv.notify_all()


def _source_render_progress(source_id: int, clip_id: int, frac: float) -> None:
    """«Generando clip 2/6» y el % total de la fase, solo mientras la fuente está generando sus clips."""
    src = db.get("sources", source_id)
    if not src or src["status"] != "rendering":
        return
    rows = db.query("SELECT id, status FROM clips WHERE source_id=? ORDER BY start", (source_id,))
    done = sum(1 for r in rows if r["status"] in ("ready", "error") and r["id"] != clip_id)
    pos = next((i for i, r in enumerate(rows) if r["id"] == clip_id), done) + 1
    db.update("sources", source_id, stage=f"Generando clip {pos}/{len(rows)}",
              stage_progress=round((done + frac) / max(1, len(rows)), 3))
    source_changed(source_id)


def render_clip(clip_id: int) -> None:
    clip = db.get("clips", clip_id)
    if clip is None:
        return
    _render_turn(clip)
    try:
        _render_clip(clip_id)
    finally:
        _render_done()


def _render_clip(clip_id: int) -> None:
    clip = db.get("clips", clip_id)
    if clip is None:
        return
    src = db.get("sources", clip["source_id"])
    out_dir = common.clip_dir(src["id"], clip_id)
    files = dict(clip.get("files") or {})
    # En directo el clip sale de un trozo exportado del buffer (files.src + src_offset).
    media_path = Path(files.get("src") or src["path"])
    offset = float(files.get("src_offset", 0.0))
    try:
        db.update("clips", clip_id, status="rendering", progress=0)
        clip_changed(clip_id)
        _source_render_progress(src["id"], clip_id, 0.0)
        transcript = common.load_transcript(src["id"])
        words = transcript.words if transcript else []
        ass_path = out_dir / "subs.ass"
        cs = clip.get("settings") or {}
        sub_words, secondary = subtitle_tracks(src, clip, transcript, cs.get("sub_lang", "orig"))
        ass_path.write_text(build_ass(sub_words, clip["start"], clip["end"], style=style_by_name(cs.get("style")),
                                      hook=clip.get("hook") or "", hook_seconds=float(cs.get("hook_seconds", 2.0)),
                                      secondary=secondary),
                            encoding="utf-8")

        last = [0.0]

        def on_progress(f: float) -> None:
            if f - last[0] >= 0.03 or f >= 1.0:
                last[0] = f
                db.update("clips", clip_id, progress=round(f, 3))
                clip_changed(clip_id)
                _source_render_progress(src["id"], clip_id, f)

        info = ffmpeg.probe(media_path)
        mp4 = out_dir / "clip.mp4"
        start, end = clip["start"] - offset, clip["end"] - offset
        plan = _reframe_plan(clip, src, media_path, start, end, out_dir)
        crop = plan.get("crop") if plan else None
        sw, sh = (crop[0], crop[1]) if crop else (info.width, info.height)
        audio_fx = _audio_plan(clip, cs, words, out_dir, plan)
        render(RenderSpec(media_path, start, end, ass_path, mp4, fps=info.fps, plan=plan, src_w=sw, src_h=sh,
                          workdir=out_dir, **audio_fx), on_progress)

        try:
            png = make_cover(clip, src, media_path, start, end, plan, sw, sh, info.fps, out_dir)
        except Exception as e:  # noqa: BLE001 - sin portada el clip sigue valiendo
            log.warning("Portada del clip %s fallida: %s", clip_id, e)
            log_event(src["id"], f"Clip {clip_id}: sin portada automática (se puede elegir en el Editor)", "warn")
            png = None
        (out_dir / "subs.srt").write_text(build_srt(sub_words, clip["start"], clip["end"]), encoding="utf-8")
        (out_dir / "wave.json").write_text(json.dumps(ffmpeg.waveform(mp4)), encoding="utf-8")
        from app.shared.music import library

        credit = library.credit(cs["music"]) if cs.get("music") else None
        write_clip_txt(db.get("clips", clip_id), src, out_dir, credits=[credit] if credit else None)
        files.update({"mp4": str(mp4), **({"png": str(png), "thumb": str(out_dir / "thumb.jpg")} if png else {}),
                      "srt": str(out_dir / "subs.srt"), "txt": str(out_dir / "info.txt"),
                      "wave": str(out_dir / "wave.json")})
        # Lo que se ha renderizado: el editor lo compara con los ajustes actuales para saber si hay cambios pendientes.
        from app.api.editor import NO_RENDER_KEYS

        rendered = {"start": clip["start"], "end": clip["end"], "hook": clip.get("hook") or "",
                    "settings": {k: v for k, v in cs.items() if k not in NO_RENDER_KEYS},
                    "preset": src.get("preset") or {}, "at": time.time()}
        db.update("clips", clip_id, status="ready", progress=1.0, files=files, rendered=rendered, error=None,
                  warnings=common.rights_warnings(src, clip["start"], clip["end"]))
        clip_changed(clip_id)
        log_event(src["id"], f"Clip listo: {clip['title']}")
    except Exception as e:  # noqa: BLE001
        log.exception("Render fallido")
        db.update("clips", clip_id, status="error", error=str(e)[:500])
        clip_changed(clip_id)
        log_event(src["id"], f"Error renderizando clip {clip_id}: {e}", "error")
    finally:
        _maybe_finish_source(src["id"])


def write_clip_txt(clip: dict, src: dict, out_dir: Path, credits: list[str] | None = None) -> Path:
    if credits is None:
        from app.shared.music import library

        music = (clip.get("settings") or {}).get("music")
        c = library.credit(music) if music else None
        credits = [c] if c else None
    return write_txt(out_dir / "info.txt", title=clip["title"], hashtags=clip.get("hashtags") or [],
                     source_title=src.get("title") or "", channel=src.get("channel") or "", url=src.get("url"),
                     warnings=common.rights_warnings(src, clip["start"], clip["end"]), credits=credits,
                     meta=clip.get("meta") or {})


def subtitle_tracks(src: dict, clip: dict, transcript, sub_lang: str):
    """(palabras de la línea principal, frases de la línea secundaria o None) según el idioma elegido.
    orig = idioma original; es / en = ese idioma (traducido si hace falta); both = ES arriba, EN debajo."""
    from app.shared.subtitles.translate import TranslationCache, sentences, to_words, translate_sentences

    words = transcript.words if transcript else []
    if not words or sub_lang in (None, "", "orig"):
        return words, None
    orig = transcript.language or "es"
    cache = TranslationCache(common.source_dir(src["id"]) / "translations.json")
    sents = sentences(words, clip["start"], clip["end"])

    def translated(lang: str):
        return translate_sentences(sents, orig, lang, cache)

    def words_in(lang: str):
        return words if lang == orig else to_words(translated(lang))

    if sub_lang in ("es", "en"):
        return words_in(sub_lang), None
    # both: principal en español, secundaria en inglés
    second = [(s.start, s.end, t) for s, t in (translated("en") if orig != "en" else [(s, s.text) for s in sents])]
    return words_in("es"), second


def make_cover(clip: dict, src: dict, media_path: Path, start: float, end: float, plan: dict | None,
               sw: int, sh: int, fps: float, out_dir: Path) -> Path:
    """Portada: fotograma elegido por el usuario o el mejor automático + texto; también la miniatura."""
    from app.shared.thumbnail.cover import best_time, render_cover

    cs = clip.get("settings") or {}
    meta = dict(clip.get("meta") or {})
    t = cs.get("cover_t")
    if t is None:
        t = meta.get("cover_auto_t")
        if t is None or not (0 <= t <= end - start):
            try:
                t = best_time(media_path, start, end, clip["peak"] - clip["start"], (plan or {}).get("crop"))
            except Exception:  # noqa: BLE001
                log.exception("Selección de portada fallida")
                t = min(max(0.5, clip["peak"] - clip["start"]), end - start - 0.5)
            meta["cover_auto_t"] = t
            db.update("clips", clip["id"], meta=meta)
    t = min(max(0.0, float(t)), max(0.0, end - start - 0.3))
    text = cs.get("cover_text") if cs.get("cover_text") is not None else clip["title"]
    png = render_cover(media_path, start, t, plan, sw, sh, text, out_dir / "cover.png", out_dir, fps)
    ffmpeg.run(["-i", str(png), "-vf", "scale=180:-2,format=yuvj420p", str(out_dir / "thumb.jpg")])
    return png


def fx_mode(cs: dict) -> str:
    """Efectos visuales del clip. Por defecto «dynamic» (solo actúan cuando el contenido lo pide);
    los clips antiguos con el zoom desactivado se quedan sin efectos."""
    if cs.get("fx") in ("none", "subtle", "dynamic"):
        return cs["fx"]
    return "none" if cs.get("zoom") is False else "dynamic"


def sfx_mode(cs: dict) -> str:
    return cs.get("sfx") if cs.get("sfx") in ("none", "auto", "subtle", "meme") else "auto"


def _audio_plan(clip: dict, cs: dict, words, out_dir: Path, plan: dict | None = None) -> dict:
    """Efectos visuales y de sonido según lo que pasa en el clip, y música (guardado en sfx.json para la timeline)."""
    from app.shared.fx import planner as fxp
    from app.shared.music import library
    from app.shared.render.vertical import plan_shots
    from app.shared.sfx.planner import sound_path

    duration = clip["end"] - clip["start"]
    peak = clip["peak"] - clip["start"]
    rel_words = [(w.start - clip["start"], w.end - clip["start"], w.text) for w in words
                 if clip["start"] <= w.start < clip["end"]]
    shots = plan_shots(plan, duration) if plan else []
    cuts = [float(s["start"]) for prev, s in zip(shots, shots[1:]) if s.get("layout") != prev.get("layout")]
    ctx = fxp.Context(duration=duration, peak=peak, hook=bool(clip.get("hook")), words=rel_words, cuts=cuts,
                      events=fxp.clip_events(common.load_events(clip["source_id"]), clip["start"], clip["end"]))
    sounds = fxp.plan_sounds(ctx, sfx_mode(cs))
    push, visual = fxp.plan_visuals(ctx, fx_mode(cs), sounds)
    sfx = []
    for e in sounds:
        p = sound_path(e.sound)
        if p is not None:
            sfx.append({"path": str(p), "t": round(e.t, 3), "gain_db": e.gain_db, "sound": e.sound, "reason": e.reason})
    zoom = next((e.t for e in visual if e.kind == "punch"), None)
    music = None
    if cs.get("music"):
        mp = library.track_path(cs["music"])
        if mp is None:
            log.warning("Música %s sin licencia permitida o inexistente: se omite", cs["music"])
        else:
            music = {"path": str(mp), "gain_db": float(cs.get("music_gain", -20)), "id": cs["music"]}
    info = {"zoom_at": zoom, "sfx": sfx, "music": music,
            "fx": {"push": push, "events": [e.to_dict() for e in visual]}}
    (out_dir / "sfx.json").write_text(json.dumps(info), encoding="utf-8")
    return info


def crop_settings(clip: dict, src: dict) -> dict:
    """Ajustes de recorte efectivos: los del clip > preset del directo > automático."""
    own = (clip.get("settings") or {}).get("crop")
    if own:
        return own
    preset = src.get("preset") or {}
    if preset:
        return preset
    if clip.get("layout_override"):
        return {"mode": "auto", "layout": clip["layout_override"]}
    return {"mode": "auto"}


def _reframe_plan(clip: dict, src: dict, media_path: Path, start: float, end: float, out_dir: Path) -> dict | None:
    """Plan de reencuadre: diseño elegido para el clip > preset del directo > automático.
    Si el análisis falla, el clip se renderiza igual con fondo desenfocado."""
    from app.clipper.reframe.planner import Tracking
    from app.clipper.reframe.service import load_or_compute

    cfg = crop_settings(clip, src)
    force = cfg.get("layout") if cfg.get("mode") != "manual" else None
    manual = {"layout": cfg.get("layout") or "follow", "boxes": cfg.get("boxes") or []} if cfg.get("mode") == "manual" else None
    if manual and manual["layout"] == "speaker":  # quién habla cambia con el tiempo: no tiene sentido fijo
        force, manual = "speaker", None
    tk = cfg.get("tracking") or {}
    tracking = Tracking(**{k: v for k, v in tk.items() if k in Tracking.__dataclass_fields__})
    try:
        transcript = common.load_transcript(src["id"])
        words = [(w.start - clip["start"], w.end - clip["start"]) for w in (transcript.words if transcript else [])
                 if clip["start"] <= w.start < clip["end"]]
        plan = load_or_compute(out_dir / "plan.json", media_path, start, end, force, tracking, manual, words)
    except Exception:  # noqa: BLE001
        log.exception("Reencuadre fallido; se usa fondo desenfocado")
        return None
    db.update("clips", clip["id"], layout=plan.get("layout", "blur"))
    return plan


def _maybe_finish_source(source_id: int) -> None:
    src = db.get("sources", source_id)
    if not src or src["status"] != "rendering":
        return
    pending = db.query("SELECT COUNT(*) AS n FROM clips WHERE source_id=? AND status IN ('pending','rendering')", (source_id,))[0]["n"]
    if pending == 0:
        db.update("sources", source_id, status="done", stage="Terminado", stage_progress=1.0)
        source_changed(source_id)
