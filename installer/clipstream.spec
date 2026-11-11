# PyInstaller: ClipStream en carpeta (onedir; arranca mucho más rápido que un único .exe).
#   .venv\Scripts\pyinstaller installer\clipstream.spec --noconfirm --distpath build\dist --workpath build\work
# Lo pesado (CUDA, ffmpeg, modelos) NO va aquí: se descarga en el primer arranque (app/core/setup.py).
from pathlib import Path

import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).parent

datas = [
    (str(ROOT / "frontend" / "dist"), "frontend/dist"),
    (str(ROOT / "assets"), "assets"),
    (str(ROOT / "installer" / "THIRD_PARTY.md"), "."),
]
binaries = []
hiddenimports = [
    "app.workers.tasks",  # tareas de Huey (se importan por nombre)
    "uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto", "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto", "uvicorn.protocols.websockets.websockets_impl", "uvicorn.lifespan.on",
]
hiddenimports += collect_submodules("app")
hiddenimports += collect_submodules("config")

for pkg in ("faster_whisper", "ctranslate2", "mediapipe", "onnxruntime", "streamlink", "yt_dlp", "webview",
            "tokenizers", "huey", "pydantic_settings"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# Biblioteca estándar COMPLETA: PyInstaller solo mete lo que detecta, y algunas dependencias (yt-dlp,
# streamlink) importan módulos por nombre en tiempo de ejecución.
STDLIB_SKIP = {"tkinter", "turtle", "turtledemo", "idlelib", "test", "lib2to3", "ensurepip", "venv", "pydoc_data",
               "curses", "readline", "tty", "pty", "termios", "posix", "pwd", "grp", "fcntl", "resource", "syslog",
               "crypt", "nis", "spwd", "ossaudiodev", "_curses", "_curses_panel", "antigravity", "this", "__phello__"}
for mod in sorted(sys.stdlib_module_names):
    if mod in STDLIB_SKIP or mod.startswith("_test"):
        continue
    hiddenimports.append(mod)
    if mod in ("unittest", "email", "xml", "json", "multiprocessing", "concurrent", "importlib", "http", "urllib",
               "logging", "asyncio", "collections", "ctypes", "sqlite3", "wsgiref", "xmlrpc", "html", "dbm", "zoneinfo",
               "encodings", "distutils", "pydoc"):
        hiddenimports += collect_submodules(mod, filter=lambda n: ".test" not in n)

# Las bibliotecas de NVIDIA (2 GB) se descargan en el primer arranque; no se empaquetan.
excludes = ["nvidia", "tensorflow", "IPython", "pytest", "tkinter", "PyInstaller",  # matplotlib: lo importa mediapipe
            # Paquetes grandes que pueda haber en el entorno de desarrollo y la app no usa (y phonemizer/espeak-ng,
            # GPL-3.0: el .exe publicado debe quedar libre de GPL).
            "torch", "torchvision", "torchaudio", "diffusers", "transformers", "accelerate", "peft",
            "kokoro_onnx", "phonemizer", "espeakng_loader"]

a = Analysis(
    [str(ROOT / "installer" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)
# Runtime de Visual C++: algún paquete arrastra un msvcp140.dll de 2017 (14.16) y ctranslate2, compilado con
# VS 2022, revienta con él (access violation en std::mutex). Se sustituyen por los del sistema (≥14.40),
# que Microsoft permite distribuir junto a la aplicación.
import os

VC_DLLS = {"msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "msvcp140_atomic_wait.dll",
           "msvcp140_codecvt_ids.dll", "vcruntime140.dll", "vcruntime140_1.dll", "concrt140.dll"}
a.binaries = [b for b in a.binaries if Path(b[0]).name.lower() not in VC_DLLS]
system32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
for name in sorted(VC_DLLS):
    src = system32 / name
    if src.exists():
        a.binaries.append((name, str(src), "BINARY"))

pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="ClipStream",
    icon=str(ROOT / "installer" / "clipstream.ico"),
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="ClipStream", upx=False)
