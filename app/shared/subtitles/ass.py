"""Subtítulos palabra por palabra en ASS (renderizados por libass en ffmpeg).

Se muestran grupos cortos de palabras (2-4) y la palabra que se está diciendo se
pinta en amarillo #F5D547. Un evento Dialogue por palabra activa.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.shared.transcription.types import Word

PLAY_W, PLAY_H = 1080, 1920


def ass_color(hex_rgb: str, alpha: int = 0) -> str:
    """'#F5D547' -> '&H0047D5F5' (ASS usa AABBGGRR)."""
    h = hex_rgb.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def ass_time(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", " ")


@dataclass
class SubStyle:
    font: str = "Montserrat ExtraBold"
    size: int = 82
    primary: str = "#FFFFFF"
    active: str = "#F5D547"
    outline: str = "#000000"
    outline_w: float = 6.0
    shadow: float = 2.0
    margin_v: int = 560  # sobre la zona segura inferior de TikTok/Reels
    uppercase: bool = True
    max_words: int = 3
    max_chars: int = 18
    border_style: int = 1  # 1 = contorno, 3 = caja opaca detrás del texto
    back: str = "#000000"
    back_alpha: int = 0x80
    active_scale: int = 100  # % de tamaño de la palabra activa


# Estilos disponibles en el editor (todas las fuentes con licencia OFL en assets/fonts).
STYLES: dict[str, SubStyle] = {
    "clasico": SubStyle(),
    "impacto": SubStyle(font="Montserrat Black", size=96, outline_w=8.0, shadow=3.0, max_words=2, max_chars=14,
                        active_scale=112),
    "limpio": SubStyle(size=62, uppercase=False, max_words=5, max_chars=28, outline_w=14.0, shadow=0.0,
                       border_style=3, back="#000000", back_alpha=0x60),
}
STYLE_LABELS = {"clasico": "Clásico", "impacto": "Impacto", "limpio": "Limpio"}


def style_by_name(name: str | None) -> SubStyle:
    return STYLES.get(name or "clasico", STYLES["clasico"])


def group_words(words: list[Word], max_words: int, max_chars: int, gap_s: float = 0.6) -> list[list[Word]]:
    """Agrupa palabras en bloques cortos; corta en fin de frase, pausa o límite."""
    groups: list[list[Word]] = []
    cur: list[Word] = []
    for w in words:
        if cur:
            chars = sum(len(x.text.strip()) + 1 for x in cur) + len(w.text.strip())
            gap = w.start - cur[-1].end
            if len(cur) >= max_words or chars > max_chars or gap >= gap_s or cur[-1].ends_sentence:
                groups.append(cur)
                cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    return groups


def build_ass(words: list[Word], clip_start: float, clip_end: float, style: SubStyle | None = None,
              hook: str = "", hook_seconds: float = 2.0,
              secondary: list[tuple[float, float, str]] | None = None) -> str:
    """Genera el archivo .ass con tiempos relativos al inicio del clip.
    `secondary`: frases (inicio, fin, texto) en otro idioma, en una línea más pequeña bajo la principal."""
    st = style or SubStyle()
    sec_size = max(36, int(st.size * 0.52))
    sec_margin = max(120, st.margin_v - int(st.size * 1.05))
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {PLAY_W}
PlayResY: {PLAY_H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,{st.font},{st.size},{ass_color(st.primary)},{ass_color(st.active)},{ass_color(st.outline if st.border_style == 1 else st.back, 0 if st.border_style == 1 else st.back_alpha)},{ass_color(st.back, st.back_alpha)},-1,0,0,0,100,100,0,0,{st.border_style},{st.outline_w},{st.shadow},2,80,80,{st.margin_v},1
Style: Sub2,{st.font},{sec_size},{ass_color('#E6E6E7')},{ass_color('#E6E6E7')},{ass_color('#000000')},{ass_color('#000000', 0x80)},-1,0,0,0,100,100,0,0,1,{max(3.0, st.outline_w * 0.6)},1,2,90,90,{sec_margin},1
Style: Hook,{st.font},64,{ass_color('#FFFFFF')},{ass_color('#FFFFFF')},{ass_color('#000000')},{ass_color('#000000', 0x60)},-1,0,0,0,100,100,0,0,3,14,0,8,90,90,330,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines: list[str] = []
    if hook:
        lines.append(f"Dialogue: 1,{ass_time(0)},{ass_time(hook_seconds)},Hook,,0,0,0,,{escape(hook)}")

    clip_words = [w for w in words if w.end > clip_start and w.start < clip_end]
    scale_on = f"\\fscx{st.active_scale}\\fscy{st.active_scale}" if st.active_scale != 100 else ""
    scale_off = "\\fscx100\\fscy100" if st.active_scale != 100 else ""
    active_tag = "{\\c" + ass_color(st.active) + scale_on + "}"
    normal_tag = "{\\c" + ass_color(st.primary) + scale_off + "}"
    events: list[list] = []  # [start, end, text]
    for group in group_words(clip_words, st.max_words, st.max_chars):
        texts = [escape(w.text.strip()) for w in group]
        if st.uppercase:
            texts = [t.upper() for t in texts]
        for i, w in enumerate(group):
            start = max(0.0, w.start - clip_start)
            # La palabra activa dura hasta que empieza la siguiente del grupo (sin huecos que parpadeen).
            nxt = group[i + 1].start if i + 1 < len(group) else w.end + 0.25
            end = min(clip_end - clip_start, max(nxt, w.end) - clip_start)
            parts = [(active_tag + t + normal_tag) if j == i else t for j, t in enumerate(texts)]
            events.append([start, end, " ".join(parts)])
    # Whisper a veces devuelve palabras solapadas: sin esto, dos líneas se verían apiladas a la vez.
    events.sort(key=lambda e: e[0])
    for k in range(len(events) - 1):
        events[k][1] = min(events[k][1], events[k + 1][0])
    for start, end, text in events:
        if end - start >= 0.01:
            lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Sub,,0,0,0,,{text}")
    for a, b, text in secondary or []:
        a, b = max(0.0, a - clip_start), min(clip_end - clip_start, b - clip_start + 0.3)
        if b - a >= 0.2 and text.strip():
            lines.append(f"Dialogue: 0,{ass_time(a)},{ass_time(b)},Sub2,,0,0,0,,{escape(text.strip())}")
    return header + "\n".join(lines) + "\n"


def build_srt(words: list[Word], clip_start: float, clip_end: float, max_words: int = 7) -> str:
    def srt_time(t: float) -> str:
        ms = int(round(max(0.0, t) * 1000))
        h, ms = divmod(ms, 3_600_000)
        m, ms = divmod(ms, 60_000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    clip_words = [w for w in words if w.end > clip_start and w.start < clip_end]
    out: list[str] = []
    for n, g in enumerate(group_words(clip_words, max_words, 42, gap_s=1.0), 1):
        out.append(f"{n}\n{srt_time(g[0].start - clip_start)} --> {srt_time(min(g[-1].end, clip_end) - clip_start)}\n"
                   + " ".join(w.text.strip() for w in g) + "\n")
    return "\n".join(out)
