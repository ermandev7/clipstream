"""Guardián de derechos para textos de microseries (política «block»).

Comprueba, en este orden:
  1. Historia: ¿está en el catálogo de dominio público? Si no, el LLM la analiza y avisa.
  2. Guion: franquicias/personajes protegidos y rasgos de adaptaciones modernas («avoid»).
  3. Letras de canciones copiadas (heurística + LLM).
  4. Citas bíblicas: solo literales de la Reina-Valera 1909 (se corrigen o se bloquean).
Si algo falla se bloquea, se explica qué y dónde, y se propone una alternativa.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import asdict, dataclass, field

from app.stories import catalog, rv1909

log = logging.getLogger(__name__)

# Franquicias y personajes con derechos (marcas, personajes originales modernos, canciones).
PROTECTED = [
    "disney", "pixar", "dreamworks", "marvel", "dc comics", "warner bros", "nintendo", "pokemon", "pokémon",
    "mickey", "minnie", "pato donald", "goofy", "pluto", "simba", "mufasa", "scar", "timón y pumba", "timon y pumba",
    "hakuna matata", "elsa", "olaf", "frozen", "moana", "vaiana", "rapunzel de disney", "buzz lightyear", "woody",
    "toy story", "shrek", "fiona", "burro de shrek", "kung fu panda", "minions", "gru", "spider-man", "spiderman",
    "hombre araña", "iron man", "capitán américa", "capitan america", "vengadores", "avengers", "thanos", "batman",
    "superman", "wonder woman", "joker", "harry potter", "hogwarts", "voldemort", "hermione", "star wars",
    "darth vader", "jedi", "yoda", "pikachu", "super mario", "luigi", "sonic", "zelda", "bob esponja", "patricio estrella",
    "los simpson", "homer simpson", "peppa pig", "paw patrol", "patrulla canina", "barbie", "transformers",
    "señor de los anillos", "frodo", "gandalf", "juego de tronos", "percy jackson", "tom hiddleston",
    "de cero a héroe", "zero to hero", "phil de hércules", "tulio y miguel", "toby tortoise", "max hare",
]
LYRIC_HINTS = re.compile(r"(\b(oh|uoh|yeah|baby|na na|la la)\b.*){2,}", re.I)


@dataclass
class Finding:
    check: str  # historia | franquicias | letras | citas
    ok: bool
    message: str
    where: str = ""
    suggestion: str = ""
    fixed_text: str | None = None


@dataclass
class GuardResult:
    blocked: bool
    findings: list[Finding] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"blocked": self.blocked, "findings": [asdict(f) for f in self.findings]}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def check_story(story_id: str | None, template: str, custom_title: str = "") -> Finding:
    """¿La historia es de dominio público?"""
    tpl = catalog.templates().get(template, {})
    if tpl.get("original"):
        return Finding("historia", True, "Historia original (plantilla propia): no se basa en obra ajena")
    story = catalog.get(story_id) if story_id else (catalog.find_by_title(custom_title) if custom_title else None)
    if story:
        return Finding("historia", True, f"{story['title']}: {story['basis']}", where=story["source"])
    if not custom_title:
        return Finding("historia", False, "Falta elegir una historia del catálogo",
                       suggestion="Elige una historia del catálogo de dominio público")
    verdict = llm_public_domain(custom_title)
    if verdict.get("public_domain") is True and verdict.get("confidence", 0) >= 0.8:
        return Finding("historia", True, f"El LLM la considera de dominio público: {verdict.get('reason', '')}",
                       where=custom_title, suggestion="Verifícalo antes de publicar")
    return Finding("historia", False,
                   f"«{custom_title}» no está en el catálogo y no se puede confirmar que sea de dominio público"
                   + (f": {verdict.get('reason')}" if verdict.get("reason") else ""),
                   where=custom_title, suggestion="Elige una historia del catálogo o una obra de autor fallecido hace más de 80 años")


def llm_public_domain(title: str) -> dict:
    from app.shared.llm import ollama

    try:
        return ollama.generate_json(
            "Eres experto en propiedad intelectual. Respondes SOLO JSON.",
            f'¿La historia «{title}» es de dominio público en España (autor fallecido hace más de 80 años, '
            'tradición oral o texto antiguo)? Considera que las adaptaciones modernas tienen derechos propios. '
            'JSON: {"public_domain": true|false, "confidence": 0-1, "reason": "máx. 25 palabras", '
            '"original_source": "obra y autor original"}')
    except Exception as e:  # noqa: BLE001
        log.warning("Sin LLM para comprobar dominio público: %s", e)
        return {"public_domain": None, "confidence": 0, "reason": "No se pudo consultar al LLM"}


def check_franchises(text: str, story: dict | None, where: str) -> Finding:
    t = _norm(text)
    hits = sorted({p for p in PROTECTED if re.search(r"\b" + re.escape(_norm(p)) + r"\b", t)})
    for term in (story or {}).get("avoid_terms", []):
        if re.search(r"\b" + re.escape(_norm(term)) + r"\b", t):
            hits.append(term)
    if hits:
        return Finding("franquicias", False, f"Menciona elementos protegidos: {', '.join(hits)}", where=where,
                       suggestion="Usa los nombres y rasgos de la fuente original, no los de adaptaciones modernas")
    return Finding("franquicias", True, "Sin franquicias ni personajes protegidos", where=where)


def check_lyrics(text: str, where: str) -> Finding:
    if LYRIC_HINTS.search(text):
        return Finding("letras", False, "Parece la letra de una canción", where=where,
                       suggestion="Narra la escena sin reproducir letras de canciones")
    return Finding("letras", True, "Sin letras de canciones", where=where)


def check_quotes(text: str, refs: list[str], where: str) -> Finding:
    fixed, checks = rv1909.check_text(text, refs)
    bad = [c for c in checks if not c.ok and not c.fixed]
    if bad:
        return Finding("citas", False, f"Cita que no está en la Reina-Valera 1909: «{bad[0].quote[:80]}»", where=where,
                       suggestion="Quita las comillas y parafrasea, o usa el texto exacto de la RV1909")
    corrected = [c for c in checks if c.fixed]
    if corrected:
        refs_txt = ", ".join(rv1909.human_ref(c.ref) for c in corrected if c.ref)
        return Finding("citas", True, f"Citas ajustadas al texto exacto de la RV1909 ({refs_txt})", where=where,
                       fixed_text=fixed)
    if checks:
        return Finding("citas", True, "Citas literales de la RV1909: " +
                       ", ".join(rv1909.human_ref(c.ref) for c in checks if c.ref), where=where)
    return Finding("citas", True, "Sin citas textuales (texto parafraseado)", where=where)


def check_text(text: str, template: str, story: dict | None, where: str) -> list[Finding]:
    out = [check_franchises(text, story, where), check_lyrics(text, where)]
    if template == "biblicas":
        out.append(check_quotes(text, (story or {}).get("refs", []), where))
    return out


def check_episode(scenes: list[dict], template: str, story: dict | None) -> GuardResult:
    """Revisa todas las escenas. Aplica las correcciones de citas en `scenes` (narration_es)."""
    findings: list[Finding] = []
    for s in scenes:
        where = f"Escena {s['idx'] + 1}"
        for f in check_text(s.get("narration_es", ""), template, story, where):
            if f.fixed_text:
                s["narration_es"] = f.fixed_text
            findings.append(f)
        # El prompt de imagen también puede pedir un personaje protegido.
        f = check_franchises(s.get("image_prompt", ""), story, f"{where} (imagen)")
        findings.append(f)
    return GuardResult(blocked=any(not f.ok for f in findings), findings=findings)
