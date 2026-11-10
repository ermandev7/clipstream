"""Informe de derechos del episodio: qué se usó, con qué licencia y qué revisó el guardián.

Se guarda junto a los vídeos (JSON para la app, TXT legible para el usuario o una plataforma).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from config.settings import ALLOWED_MODELS

from app.shared.rights import licenses

MODELS_USED = {
    "Guion": ("ollama/qwen2.5:7b-instruct", "Qwen2.5 7B Instruct"),
    "Voz": ("kokoro/v1.0", "Kokoro-82M v1.0"),
    "Tiempos de subtítulos": ("faster-whisper/large-v3-turbo", "Whisper large-v3-turbo"),
    "Imágenes": ("stabilityai/sdxl-base-1.0", "Stable Diffusion XL base 1.0 + SDXL-Lightning"),
    "Revisión visual": ("openai/clip-vit-large-patch14", "CLIP ViT-L/14"),
}


def build(series: dict, episode: dict, scenes: list[dict], music: Path | None, sfx: Path | None,
          videos: dict[str, str]) -> dict:
    story = series.get("story") or {}
    rights = episode.get("rights") or {}
    imgs = []
    for s in scenes:
        chk = ((s.get("files") or {}).get("image") or {}).get("check") or {}
        imgs.append({"escena": s["idx"] + 1, "ok": chk.get("ok", False), "parecido_max": chk.get("match", ""),
                     "probabilidad": chk.get("prob", 0)})
    return {
        "generado": time.strftime("%Y-%m-%d %H:%M"),
        "serie": series["name"], "episodio": episode["number"], "titulo": episode.get("title") or "",
        "historia": {"titulo": story.get("title") or series["name"], "fuente": story.get("source") or "Original",
                     "base_legal": story.get("basis") or "Historia original creada para esta serie",
                     "evitar": story.get("avoid") or []},
        "texto": [{"revision": f.get("check"), "ok": f.get("ok"), "detalle": f.get("message"), "donde": f.get("where", "")}
                  for f in (rights.get("findings") or [])],
        "imagenes": imgs,
        "musica": _asset(music), "efectos": _asset(sfx),
        "modelos": [{"uso": k, "modelo": name, "licencia": ALLOWED_MODELS.get(mid, "?")}
                    for k, (mid, name) in MODELS_USED.items()],
        "ia": "Imágenes, voz y guion generados con IA. Los vídeos llevan la etiqueta visible «Contenido generado con IA».",
        "videos": videos,
        "bloqueos": [s["idx"] + 1 for s in scenes if s["status"] == "blocked"],
    }


def _asset(p: Path | None) -> dict | None:
    if not p:
        return None
    e = licenses.entry(p) or {}
    return {"archivo": p.name, "titulo": e.get("title", p.stem), "autor": e.get("author", ""),
            "licencia": e.get("license", "?"), "credito": licenses.credit_line(p)}


def to_text(r: dict) -> str:
    h = r["historia"]
    lines = [f"INFORME DE DERECHOS · {r['serie']} · Episodio {r['episodio']}: {r['titulo']}", f"Generado: {r['generado']}", "",
             "HISTORIA", f"  {h['titulo']} — {h['fuente']}", f"  Base legal: {h['base_legal']}"]
    if h["evitar"]:
        lines.append("  Se evita todo lo propio de: " + "; ".join(h["evitar"]))
    lines += ["", "REVISIÓN DEL TEXTO"]
    lines += _text_summary(r["texto"]) or ["  (sin revisiones registradas)"]
    lines += ["", "REVISIÓN DE IMÁGENES (parecido con personajes protegidos)"]
    lines += [f"  Escena {i['escena']}: {'OK' if i['ok'] else 'BLOQUEADA'} · más parecido: {i['parecido_max']} ({i['probabilidad']:.0%})"
              for i in r["imagenes"]]
    lines += ["", "AUDIO"]
    for k, label in (("musica", "Música"), ("efectos", "Efectos")):
        a = r[k]
        lines.append(f"  {label}: {a['titulo']} · {a['autor']} · {a['licencia']}" if a else f"  {label}: ninguno")
    lines += ["", "MODELOS DE IA"] + [f"  {m['uso']}: {m['modelo']} ({m['licencia']})" for m in r["modelos"]]
    lines += ["", "ETIQUETA DE IA", f"  {r['ia']}", "", "VÍDEOS"] + [f"  {k}: {v}" for k, v in r["videos"].items()]
    if r["bloqueos"]:
        lines += ["", f"ATENCIÓN: escenas bloqueadas {r['bloqueos']}: este episodio no debe publicarse."]
    return "\n".join(lines) + "\n"


NAMES = {"historia": "Historia", "franquicias": "Franquicias y personajes protegidos", "letras": "Letras de canciones",
         "citas": "Citas textuales"}


def _text_summary(findings: list[dict]) -> list[str]:
    """Una línea por tipo de revisión; los problemas y las correcciones, uno a uno."""
    out = []
    for check in dict.fromkeys(f["revision"] for f in findings):
        group = [f for f in findings if f["revision"] == check]
        bad = [f for f in group if not f["ok"]]
        fixed = [f for f in group if f["ok"] and "ajustadas" in (f["detalle"] or "").lower()]  # citas corregidas a la RV1909
        label = NAMES.get(check, check)
        out.append(f"  [{'BLOQUEO' if bad else 'OK'}] {label}: {len(group) - len(bad)}/{len(group)} revisiones correctas")
        out += [f"      - {f['donde']}: {f['detalle']}" for f in bad + fixed]
    return out


def write(r: dict, folder: Path, stem: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{stem}_derechos.json").write_text(json.dumps(r, indent=2, ensure_ascii=False), encoding="utf-8")
    txt = folder / f"{stem}_derechos.txt"
    txt.write_text(to_text(r), encoding="utf-8")
    return txt
