"""Guion de microseries: biblia de la serie, escaleta y guion por episodio con continuidad."""
from __future__ import annotations

import logging
import re

from app.shared.llm import ollama
from app.stories import catalog, rv1909

log = logging.getLogger(__name__)
WORDS_PER_SECOND = 2.8  # Kokoro a velocidad 0,92 (medido)
MIN_SCENE_S, MAX_SCENE_S = 3.5, 13.0
TARGET_WORDS = 210  # ~75 s de narración: el spec pide episodios de 60-90 s
MIN_WORDS = 165  # por debajo, el guion se alarga en una segunda pasada

SYSTEM = """Eres guionista de microseries verticales (TikTok, Reels, Shorts) basadas en historias de dominio público.
Escribes en español neutro, con frases cortas, ritmo ágil y un gancho en la primera escena.
Nunca usas personajes, nombres, diseños, frases ni canciones de adaptaciones modernas con derechos
(Disney, Pixar, DreamWorks, Marvel, etc.): solo la fuente original. Respondes SOLO con JSON válido."""

BIBLE_PROMPT = """Plantilla: {template_label} · Tono: {tone}
Historia: {title} — Fuente original: {source}
{source_notes}
Número de episodios: {episodes}

{cast_rule}
Prepara la biblia de la serie. JSON:
{{"name": "título de la serie (máx. 40 caracteres)",
 "synopsis": "2-3 frases",
 "characters": [{{"name": "...", "role": "protagonista|antagonista|secundario|narrador",
                  "description": "personalidad en 1 frase (español)",
                  "kind": "QUÉ ES, en inglés, 1-3 palabras: lion, mouse, young man, old woman, red apple",
                  "visual": "aspecto FIJO en inglés para generar imágenes: rasgos, colores, ropa (máx. 25 palabras, sin el nombre)"}}],
 "episodes": [{{"number": 1, "title": "...", "summary": "1-2 frases"}}]}}
Máximo 6 personajes. Exactamente {episodes} episodios que cuenten la historia completa en orden."""

EPISODE_PROMPT = """Serie: {name} ({template_label}) · Tono: {tone}
Historia original: {title} — {source}
{source_notes}
Personajes (usa SIEMPRE su aspecto fijo en los prompts de imagen):
{characters}

Escaleta completa:
{outline}

Lo que ya ha pasado (continuidad):
{previous}

Escribe el EPISODIO {number}: «{ep_title}» — {ep_summary}
{quote_rule}
Duración total 60-90 segundos: entre 7 y 10 escenas de 5-9 segundos de narración cada una.
La escena 1 es un gancho. La última deja intriga para el siguiente episodio (salvo si es el final).
JSON:
{{"title": "...", "summary": "2 frases de lo que pasa (para la continuidad)",
 "scenes": [{{"narration_es": "texto que lee el narrador (25-32 palabras, nunca menos de 22)",
             {en_field}"subject": "in English, max 15 words: WHO does WHAT, using what they are (a lion, a tiny mouse), never names",
             "setting": "in English, max 12 words: place, time of day, light",
             "characters": ["nombres EXACTOS de la lista que se ven, el principal primero, máximo 2"]}}]}}"""


EXPAND_PROMPT = """Este guion de la microserie «{name}» es demasiado corto para un episodio de 60-90 segundos.
Añade a CADA escena una o dos frases más (detalle visual, lo que siente el personaje, tensión; sin diálogos nuevos), hasta
unas {words} palabras por escena, SIN inventar hechos nuevos ni cambiar el orden. Español correcto. Conserva EXACTAS las frases que ya van entre comillas.
{quote_rule}
Escenas:
{scenes}
JSON: {{"scenes": [{{"narration_es": "...", {en_field}}}]}} con exactamente {n} escenas en el mismo orden."""


def _expand(series: dict, scenes: list[dict], tpl: dict, with_en: bool) -> None:
    """El modelo local tiende a escribir escenas de 10-14 palabras: se alargan en sitio."""
    words = max(22, min(36, round(TARGET_WORDS / len(scenes))))
    listing = "\n".join(f"{i + 1}. {s['narration_es']}" for i, s in enumerate(scenes))
    res = ollama.generate_json(SYSTEM, EXPAND_PROMPT.format(
        name=series["name"], words=words, quote_rule=tpl.get("quote_rule", ""), scenes=listing, n=len(scenes),
        en_field='"narration_en": "same in natural English, with English name spellings"' if with_en else ""), temperature=0.6, num_ctx=8192)
    longer = [s for s in (res.get("scenes") or []) if isinstance(s, dict)]
    if len(longer) != len(scenes):
        log.warning("Alargar guion: %d escenas devueltas de %d; se deja el corto", len(longer), len(scenes))
        return
    for s, new in zip(scenes, longer, strict=True):
        text = re.sub(r"\s+", " ", str(new.get("narration_es", ""))).strip()
        if len(text.split()) > len(s["narration_es"].split()):
            s["narration_es"] = text
            if with_en and str(new.get("narration_en", "")).strip():
                s["narration_en"] = re.sub(r"\s+", " ", str(new["narration_en"])).strip()
            s["duration"] = estimate_duration(text)


EXPAND_ONE_PROMPT = """Microserie «{name}». Escena anterior: {prev}
Escena a alargar: «{text}»
Escena siguiente: {next}
Reescríbela con entre 24 y 32 palabras: mismos hechos, añade lo que se ve, se oye y lo que siente el
personaje. NO añadas diálogos ni frases nuevas entre comillas; conserva EXACTAS las que ya haya. {quote_rule}
JSON: {{"narration_es": "...", {en_field}}}"""
SHORT_SCENE = 21  # palabras


def expand_short_scenes(series: dict, scenes: list[dict], tpl: dict, with_en: bool) -> None:
    """Segundo nivel: una petición pequeña por escena corta (el 7B sigue mejor una instrucción concreta)."""
    for i, s in enumerate(scenes):
        n = len(s["narration_es"].split())
        if n >= SHORT_SCENE:
            continue
        res = ollama.generate_json(SYSTEM, EXPAND_ONE_PROMPT.format(
            name=series["name"], prev=scenes[i - 1]["narration_es"] if i else "(inicio)", text=s["narration_es"],
            next=scenes[i + 1]["narration_es"] if i + 1 < len(scenes) else "(fin)", words=28,
            quote_rule=tpl.get("quote_rule", ""),
            en_field='"narration_en": "same in natural English"' if with_en else ""), temperature=0.6)
        text = re.sub(r"\s+", " ", str(res.get("narration_es", ""))).strip()
        if not n < len(text.split()) <= 55:
            log.info("Escena %d: alargado descartado (%d -> %d palabras)", i + 1, n, len(text.split()))
        else:
            s["narration_es"] = text
            if with_en and str(res.get("narration_en", "")).strip():
                s["narration_en"] = re.sub(r"\s+", " ", str(res["narration_en"])).strip()
            s["duration"] = estimate_duration(text)


def _source_notes(story: dict | None, template: str) -> str:
    if not story:
        return "Historia original creada para esta serie (no adaptar obras ajenas)."
    notes = [f"Base legal: {story['basis']}."]
    if story.get("avoid"):
        notes.append("NO uses nada propio de: " + "; ".join(story["avoid"]) + ".")
    if template == "biblicas" and story.get("refs"):
        verses = []
        for ref in story["refs"][:3]:
            for key, text in rv1909.chapter(ref)[:12]:
                verses.append(f"{rv1909.human_ref(key)}: {text}")
        notes.append("Versículos de la Reina-Valera 1909 que puedes citar LITERALMENTE:\n" + "\n".join(verses))
    return "\n".join(notes)


def series_bible(template: str, story: dict | None, episodes: int, custom_title: str = "") -> dict:
    tpl = catalog.templates()[template]
    title = story["title"] if story else (custom_title or "Historia original")
    source = story["source"] if story else ("Original" if tpl.get("original") else custom_title)
    cast = (story or {}).get("characters") or []
    cast_rule = ("Personajes principales de la fuente: " + ", ".join(cast) + ". NO inventes personajes que no estén en "
                 "la fuente original (ni animales ni personas nuevas).") if cast else ""
    res = ollama.generate_json(SYSTEM, BIBLE_PROMPT.format(
        template_label=tpl["label"], tone=tpl["tone"], title=title, source=source, cast_rule=cast_rule,
        source_notes=_source_notes(story, template), episodes=episodes), temperature=0.6, num_ctx=8192)
    chars = []
    for c in (res.get("characters") or [])[:6]:
        if isinstance(c, dict) and c.get("name"):
            chars.append({"name": str(c["name"])[:40], "role": str(c.get("role", ""))[:20],
                          "description": str(c.get("description", ""))[:200],
                          "kind": clean_kind(c.get("kind", "")), "visual": str(c.get("visual", ""))[:300]})
    if template == "fabulas":  # las fábulas traen su reparto completo en el catálogo
        chars = keep_source_cast(chars, cast)
    for c, v in zip(chars, to_english([c["visual"] for c in chars]), strict=True):
        c["visual"] = v[:300]
    eps = []
    for i, e in enumerate((res.get("episodes") or [])[:episodes]):
        eps.append({"number": i + 1, "title": str(e.get("title", f"Episodio {i + 1}"))[:80],
                    "summary": str(e.get("summary", ""))[:300]})
    while len(eps) < episodes:
        eps.append({"number": len(eps) + 1, "title": f"Episodio {len(eps) + 1}", "summary": ""})
    return {"name": str(res.get("name") or title)[:60], "synopsis": str(res.get("synopsis", ""))[:600],
            "characters": chars, "episodes": eps, "style": tpl["style"], "tone": tpl["tone"]}


def estimate_duration(text: str) -> float:
    words = len(text.split())
    return round(min(MAX_SCENE_S, max(MIN_SCENE_S, words / WORDS_PER_SECOND + 0.6)), 1)


SPANISH_RE = re.compile(r"[áéíóúñ¿¡]|\b(el|la|los|las|del|al|una|unos|con|pero|está|sobre|entre|y)\b", re.I)
TRANSLATE_PROMPT = """Translate each line to natural English for an image generator. Keep the same order and count.
Only translate; do not add anything.
{lines}
JSON: {{"lines": ["...", "..."]}}"""


def looks_spanish(text: str) -> bool:
    hits = len(SPANISH_RE.findall(text or ""))
    return hits >= 2 or (hits >= 1 and len(text.split()) <= 6)  # frases cortas: «el sol, cuela»


def to_english(texts: list[str]) -> list[str]:
    """El 7B a veces escribe en español lo que pedimos en inglés; SDXL y CLIP lo entienden mucho peor."""
    idx = [i for i, t in enumerate(texts) if looks_spanish(t)]
    if not idx:
        return texts
    res = ollama.generate_json("You are a precise translator. Answer only JSON.", TRANSLATE_PROMPT.format(
        lines="\n".join(f"{n + 1}. {texts[i]}" for n, i in enumerate(idx))), temperature=0.1)
    out = list(texts)
    lines = [str(x) for x in (res.get("lines") or [])]
    if len(lines) == len(idx):
        for i, t in zip(idx, lines, strict=True):
            out[i] = re.sub(r"^\d+\.\s*", "", t).strip() or texts[i]
    else:
        log.warning("Traducción de prompts: %d líneas de %d; se dejan como están", len(lines), len(idx))
    return out


def clean_kind(k) -> str:
    k = re.sub(r"[^a-zA-Z \-]", "", str(k)).strip().lower()
    return " ".join(k.split()[:3])


def _key(name: str) -> str:
    import unicodedata

    n = unicodedata.normalize("NFKD", str(name).lower())
    n = "".join(c for c in n if not unicodedata.combining(c))
    n = re.sub(r"\(.*?\)", "", n)
    return re.sub(r"^(el|la|los|las|the)\s+", "", re.sub(r"[^a-z ]", "", n).strip())


def keep_source_cast(chars: list[dict], cast: list[str]) -> list[dict]:
    """Solo el reparto de la fuente (el 7B inventa: un zorro y una golondrina en «El león y el ratón»)."""
    if not cast:
        return chars
    keys = [_key(c) for c in cast]
    out = [c for c in chars if any(k == ck or k.startswith(ck) or ck.startswith(k)
                                   for ck in keys for k in [_key(c["name"])] if ck and k)]
    return out or chars


def match_characters(names: list, characters: list[dict]) -> list[str]:
    """Nombres de la escena -> nombres canónicos de la biblia («León» -> «El león»). Máximo 2 (SDXL no da más)."""
    import difflib

    canon = {_key(c["name"]): c["name"] for c in characters}
    out: list[str] = []
    for n in names:
        k = _key(n)
        hit = canon.get(k) or next((v for ck, v in canon.items() if ck and (ck.startswith(k) or k.startswith(ck))), None)
        if not hit:
            close = difflib.get_close_matches(k, list(canon), n=1, cutoff=0.6)
            hit = canon[close[0]] if close else None
        if hit and hit not in out:
            out.append(hit)
    return out[:2]


def visual_of(c: dict) -> str:
    """Aspecto para SDXL: siempre empieza por lo que es (sin el tipo, SDXL no sabe que «León» es un león)."""
    kind, vis = c.get("kind") or "", re.sub(r"\s+", " ", c.get("visual") or "").strip().rstrip(".")
    if kind and kind not in vis.lower()[:60]:
        vis = f"{kind}, {vis}" if vis else kind
    return vis


def build_image_prompt(subject: str, names: list[str], characters: list[dict], setting: str, style: str) -> str:
    """Lo importante primero: el bloque de 77 tokens que más pesa en SDXL es el primero."""
    by_name = {c["name"]: c for c in characters}
    parts = [re.sub(r"\s+", " ", str(subject)).strip().rstrip(".")]
    for n in names:
        if n in by_name:
            parts.append(visual_of(by_name[n]))
    if str(setting).strip():
        parts.append(re.sub(r"\s+", " ", str(setting)).strip().rstrip("."))
    return clean_prompt(". ".join(p for p in parts if p), style)


def clean_prompt(p: str, style: str) -> str:
    p = re.sub(r"\s+", " ", str(p)).strip().rstrip(".")
    return f"{p}, {style}, vertical composition, no text, no watermark" if p else style


def episode_script(series: dict, characters: list[dict], number: int, previous: list[dict]) -> dict:
    bible = series.get("bible") or {}
    template = series["template"]
    tpl = catalog.templates()[template]
    story = catalog.get(series.get("story_id"))
    outline = bible.get("episodes") or []
    ep = next((e for e in outline if e["number"] == number), {"title": f"Episodio {number}", "summary": ""})
    langs = series.get("languages") or ["es"]
    prev_txt = "\n".join(f"Ep. {p['number']} «{p['title']}»: {p['summary']}" for p in previous) or "Es el primer episodio."
    res = ollama.generate_json(SYSTEM, EPISODE_PROMPT.format(
        name=series["name"], template_label=tpl["label"], tone=tpl["tone"],
        title=story["title"] if story else series["name"], source=story["source"] if story else "Original",
        source_notes=_source_notes(story, template),
        characters="\n".join(f"- {c['name']} ({c.get('role', '')}): {c.get('description', '')} | look: {c.get('visual', '')}"
                             for c in characters) or "- (narrador)",
        outline="\n".join(f"{e['number']}. {e['title']}: {e.get('summary', '')}" for e in outline),
        previous=prev_txt, number=number, ep_title=ep["title"], ep_summary=ep.get("summary", ""),
        quote_rule=tpl.get("quote_rule", "No pongas citas entre comillas de obras con derechos."),
        en_field='"narration_en": "same narration in natural English", ' if "en" in langs else ""),
        temperature=0.7, num_ctx=8192)
    scenes = []
    style = bible.get("style") or tpl["style"]
    for i, s in enumerate((res.get("scenes") or [])[:12]):
        if not isinstance(s, dict) or not str(s.get("narration_es", "")).strip():
            continue
        text = re.sub(r"\s+", " ", str(s["narration_es"])).strip()
        names = match_characters(s.get("characters") or [], characters)
        scenes.append({"idx": len(scenes), "narration_es": text,
                       "narration_en": re.sub(r"\s+", " ", str(s.get("narration_en", ""))).strip(),
                       "_subject": str(s.get("subject") or s.get("image_prompt") or ""),
                       "_setting": str(s.get("setting") or ""),
                       "characters": names,
                       "duration": estimate_duration(text)})
    english = to_english([s["_subject"] for s in scenes] + [s["_setting"] for s in scenes])
    for i, s in enumerate(scenes):
        subject, setting = english[i], english[len(scenes) + i]
        s.pop("_subject"), s.pop("_setting")
        s["image_prompt"] = build_image_prompt(subject, s["characters"], characters, setting, style)
    if len(scenes) < 3:
        raise ValueError("El LLM no devolvió un guion válido (menos de 3 escenas); vuelve a generarlo")
    if sum(len(s["narration_es"].split()) for s in scenes) < MIN_WORDS:
        _expand(series, scenes, tpl, "en" in langs)
    if sum(len(s["narration_es"].split()) for s in scenes) < MIN_WORDS:
        expand_short_scenes(series, scenes, tpl, "en" in langs)
    return {"title": str(res.get("title") or ep["title"])[:80], "summary": str(res.get("summary") or ep.get("summary", ""))[:400],
            "scenes": scenes}
