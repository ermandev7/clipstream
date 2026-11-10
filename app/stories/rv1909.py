"""Biblia Reina-Valera 1909 (dominio público): referencias y verificación de citas literales.

Regla de las microseries bíblicas: las citas textuales (entre comillas) solo pueden ser de la
RV1909; todo lo demás va parafraseado. Una cita casi idéntica (ortografía moderna, «creó» por
«crió», «fue» por «fué») se corrige al texto exacto; una cita que no existe se bloquea.
"""
from __future__ import annotations

import difflib
import json
import re
import unicodedata
from dataclasses import dataclass
from functools import cache

from config.settings import ASSETS_DIR

BOOKS = {
    "génesis": "GEN", "éxodo": "EXO", "levítico": "LEV", "números": "NUM", "deuteronomio": "DEU", "josué": "JOS",
    "jueces": "JDG", "rut": "RUT", "1 samuel": "1SA", "2 samuel": "2SA", "1 reyes": "1KI", "2 reyes": "2KI",
    "1 crónicas": "1CH", "2 crónicas": "2CH", "esdras": "EZR", "nehemías": "NEH", "ester": "EST", "job": "JOB",
    "salmos": "PSA", "proverbios": "PRO", "eclesiastés": "ECC", "cantares": "SNG", "isaías": "ISA", "jeremías": "JER",
    "lamentaciones": "LAM", "ezequiel": "EZK", "daniel": "DAN", "oseas": "HOS", "joel": "JOL", "amós": "AMO",
    "abdías": "OBA", "jonás": "JON", "miqueas": "MIC", "nahúm": "NAM", "habacuc": "HAB", "sofonías": "ZEP",
    "hageo": "HAG", "zacarías": "ZEC", "malaquías": "MAL", "mateo": "MAT", "marcos": "MRK", "lucas": "LUK",
    "juan": "JHN", "hechos": "ACT", "romanos": "ROM", "1 corintios": "1CO", "2 corintios": "2CO", "gálatas": "GAL",
    "efesios": "EPH", "filipenses": "PHP", "colosenses": "COL", "1 tesalonicenses": "1TH", "2 tesalonicenses": "2TH",
    "1 timoteo": "1TI", "2 timoteo": "2TI", "tito": "TIT", "filemón": "PHM", "hebreos": "HEB", "santiago": "JAS",
    "1 pedro": "1PE", "2 pedro": "2PE", "1 juan": "1JN", "2 juan": "2JN", "3 juan": "3JN", "judas": "JUD",
    "apocalipsis": "REV",
}
QUOTE_RE = re.compile(r"[«\"“‘']([^»\"”’']{12,})[»\"”’']")  # « » “ ” " ' ‘ ’
FIX_THRESHOLD = 0.82  # parecido mínimo para corregir una cita al texto exacto


@cache
def verses() -> dict[str, str]:
    return json.loads((ASSETS_DIR / "texts" / "rv1909.json").read_text(encoding="utf-8"))


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-zñ0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _norm_map(s: str) -> tuple[str, list[int]]:
    """norm() carácter a carácter, guardando de qué posición del original sale cada uno."""
    out: list[str] = []
    idx: list[int] = []
    for i, ch in enumerate(s):
        base = "".join(c for c in unicodedata.normalize("NFKD", ch.lower()) if not unicodedata.combining(c))
        base = base if re.fullmatch(r"[a-zñ0-9]*", base) else " "
        for b in base:
            if b == " " and (not out or out[-1] == " "):
                continue
            out.append(b)
            idx.append(i)
    while out and out[-1] == " ":
        out.pop()
        idx.pop()
    return "".join(out), idx


def exact_span(verse: str, quote_norm: str) -> str | None:
    """Trozo del versículo original que corresponde a la cita normalizada."""
    vn, idx = _norm_map(verse)
    pos = vn.find(quote_norm)
    if pos < 0:
        return None
    return verse[idx[pos]: idx[pos + len(quote_norm) - 1] + 1]


@cache
def _norm_index() -> list[tuple[str, str]]:
    return [(k, norm(v)) for k, v in verses().items()]


def code(ref: str) -> tuple[str, int] | None:
    """'Jonás 1' -> ('JON', 1)"""
    m = re.match(r"^\s*(\d?\s*[^\d]+?)\s+(\d+)\s*$", ref)
    if not m:
        return None
    book = re.sub(r"\s+", " ", m.group(1).strip().lower())
    c = BOOKS.get(book)
    return (c, int(m.group(2))) if c else None


def chapter(ref: str) -> list[tuple[str, str]]:
    c = code(ref)
    if not c:
        return []
    prefix = f"{c[0]} {c[1]}:"
    return [(k, v) for k, v in verses().items() if k.startswith(prefix)]


def human_ref(key: str) -> str:
    """'JON 1:2' -> 'Jonás 1:2'"""
    book, cv = key.split(" ")
    name = next((n for n, c in BOOKS.items() if c == book), book)
    return f"{name[0].upper()}{name[1:]} {cv}"


@dataclass
class QuoteCheck:
    quote: str
    ok: bool  # existe tal cual (salvo tildes/puntuación)
    fixed: str | None  # texto exacto RV1909 si se ha podido corregir
    ref: str | None
    score: float


def check_quote(quote: str, refs: list[str] | None = None) -> QuoteCheck:
    """Busca la cita en los capítulos de la historia y, si no, en toda la Biblia."""
    q = norm(quote)
    if not q:
        return QuoteCheck(quote, False, None, None, 0.0)
    pools: list[list[tuple[str, str]]] = []
    if refs:
        pools.append([(k, norm(v)) for r in refs for k, v in chapter(r)])
    pools.append(_norm_index())
    best = (0.0, None)
    for pool in pools:
        for key, text in pool:
            if q in text:  # fragmento del versículo (salvo tildes/puntuación): se deja con la grafía de 1909
                exact = exact_span(verses()[key], q)
                return QuoteCheck(quote, True, exact if exact and exact != quote.strip() else None, key, 1.0)
            if abs(len(text) - len(q)) > max(len(q), len(text)) * 0.6:
                continue
            r = difflib.SequenceMatcher(None, q, text).quick_ratio()
            if r > best[0] and r >= FIX_THRESHOLD - 0.1:
                r = difflib.SequenceMatcher(None, q, text).ratio()
                if r > best[0]:
                    best = (r, key)
        if best[0] >= FIX_THRESHOLD:
            break
    score, key = best
    if key and score >= FIX_THRESHOLD:
        return QuoteCheck(quote, False, verses()[key], key, round(score, 3))
    return QuoteCheck(quote, False, None, key, round(score, 3))


def check_text(text: str, refs: list[str] | None = None) -> tuple[str, list[QuoteCheck]]:
    """Revisa todas las citas entre comillas. Devuelve (texto con citas corregidas, revisiones)."""
    checks: list[QuoteCheck] = []
    out = text
    for m in QUOTE_RE.finditer(text):
        c = check_quote(m.group(1), refs)
        checks.append(c)
        if c.fixed:
            out = out.replace(m.group(1), c.fixed.rstrip(".:;,"))
    return out, checks
