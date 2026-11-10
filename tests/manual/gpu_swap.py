"""Prueba manual: Whisper y LLM se cargan por turnos y la VRAM no pasa de 8 GB.

    .venv\\Scripts\\python tests\\manual\\gpu_swap.py storage\\testdata\\voz.wav
"""
import logging
import sys
import time

sys.path.insert(0, ".")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

from app.core.gpu import gpu  # noqa: E402
from app.shared.llm import ollama  # noqa: E402
from app.shared.transcription import whisper  # noqa: E402

audio = sys.argv[1]
peak = 0.0


def vram(label: str) -> None:
    global peak
    v = gpu.vram()
    peak = max(peak, v.used_gb)
    print(f"[{label:<22}] VRAM {v.used_gb:.2f} / {v.total_gb:.1f} GB  activo={gpu.active}")


vram("inicio")
t0 = time.perf_counter()
tr = whisper.transcribe(audio)
print(f"Whisper: {len(tr.words)} palabras en {time.perf_counter() - t0:.1f} s (incluye carga)")
print("  ", " ".join(f"{w.text}@{w.start:.1f}" for w in tr.words[:12]))
vram("tras whisper")

t0 = time.perf_counter()
res = ollama.generate_json("Responde solo JSON.", f'Puntúa 0-100 lo viral de: "{tr.text_between(0, 999)}". JSON: {{"score": int, "reason": str}}')
print(f"LLM: {res} en {time.perf_counter() - t0:.1f} s")
vram("tras LLM")

t0 = time.perf_counter()
tr2 = whisper.transcribe(audio)
print(f"Whisper otra vez: {len(tr2.words)} palabras en {time.perf_counter() - t0:.1f} s")
vram("whisper de nuevo")

gpu.release()
time.sleep(1)
vram("liberado")
print(f"PICO VRAM: {peak:.2f} GB  ->  {'OK' if peak < 8.0 else 'EXCEDE 8 GB'}")
