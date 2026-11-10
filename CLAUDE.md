# ClipStream — reglas del proyecto

Especificación completa: `CLIPSTREAM_PROMPT.md`. Las decisiones ahí son definitivas; no se rediscuten. Si algo es imposible, avisar y proponer la alternativa más cercana.

## Forma de trabajar
- Hablar con el usuario en **español**.
- Pasos pequeños y verificables; probar cada pieza en la máquina real antes de seguir.
- Avisar (qué y cuánto ocupa) antes de descargar cualquier modelo > 1 GB.
- `git` con commits pequeños y descriptivos.
- Tests para lógica pura (detección, cortes, subtítulos). Lo que depende de GPU/red: prueba manual con vídeo corto.
- Verificar licencia oficial antes de añadir cualquier dependencia o modelo.
- Fase actual: **Fase 1 — MVP del clipper** (sección 10 del spec). No adelantar fases.

## Decisiones fijadas
- Usuario único, sin cuentas, pagos ni servidor. La app no se vende.
- Windows, **RTX 3060 8 GB VRAM** (la memoria compartida no cuenta).
- Idiomas: español + inglés.
- Motor en **Python 3.11+**: FastAPI + WebSocket, SQLite, cola **Huey con SQLite** (nunca Redis), sin Docker.
- Vídeo: ffmpeg (NVENC), streamlink, yt-dlp.
- Frontend: React + Vite + TypeScript.
- Empaquetado: PyInstaller + pywebview + Inno Setup. Modelos IA se descargan en el primer uso, no van en el .exe.
- Whisper y LLM se cargan **por turnos** vía `app/core/gpu.py` (gestor de VRAM que serializa tareas pesadas).
- Derechos: clips = **avisar, nunca bloquear**; microseries = **avisar y bloquear**.
- Solo recursos gratuitos con licencia comercial: `ALLOWED_LICENSES = ["CC0","CC-BY","Apache-2.0","MIT","OFL","Pixabay","YouTube-AudioLibrary"]`.
- Prohibido: Ultralytics YOLO, FLUX.1 dev, HunyuanVideo, XTTS-v2, pesos F5-TTS, Wav2Lip, MusicGen, ACRCloud/AudD, Redis.
- Acceso opcional desde el móvil en la misma WiFi.

## Diseño de interfaz
Herramienta de edición profesional (estilo DaVinci/Premiere). Nada con aspecto "hecho con IA": sin degradados, sin tarjetas muy redondeadas, sin emojis, sin colores chillones.

| Token | Valor |
|---|---|
| Fondo | `#141415` |
| Panel / cabecera | `#1A1A1C` |
| Control | `#1F1F22` |
| Borde | `#2C2C2F` líneas, `#36363A` controles |
| Texto | `#E6E6E7` / secundario `#A1A1A6` |
| Acento | `#4C8DFF`; segmento activo `#2A3A57` |
| En vivo / bloqueo | `#E5484D` / texto `#F2777B` |
| Aviso | `#D9A441` / texto `#E0B861` |
| Correcto | `#8FD19E` |
| Subtítulo palabra activa | `#F5D547` |

- Fuentes: IBM Plex Sans (UI), IBM Plex Mono (tiempos, datos técnicos).
- Radio 4 px en controles; paneles sin radio, separados por líneas de 1 px.
- Base 13 px; etiquetas de sección 11 px mayúsculas con letter-spacing.
- Tablas compactas, inspectores etiqueta/valor, controles segmentados, pestañas con subrayado de acento.
- Responsive: en móvil los paneles se apilan.
- Cabecera 48 px: "ClipStream" · pestañas Fuentes | Editor | Recorte | Microseries | Exportar · derecha en mono "RTX 3060 · VRAM x / 8.0 GB" (dato real).

## Entorno detectado (2026-11-09)
- Windows 11 Pro 26200 · RTX 3060 8 GB · driver 610.88 · CUDA UMD 13.3 · sin CUDA toolkit (no hace falta: wheels traen runtime).
- Python 3.11 (`%LOCALAPPDATA%\Programs\Python\Python311\python.exe`) para el venv `.venv`.
- Node 22.13 / npm 10.9 · git 2.43 · Ollama 0.40.15 · winget disponible.

## Estado Fase 1 (2026-11-09)
Hecho y probado en la máquina: ingesta (local, vídeo publicado YouTube, directo YouTube con buffer), señales
(Whisper por palabra, picos de audio, chat YouTube vía yt-dlp), detección (scorer, llm_judge, boundaries, dedup +
selección variada), render 9:16 desenfocado + ASS + NVENC + loudnorm dos pasadas, API + WebSocket, Huey SQLite,
UI Fuentes + Exportar, ventana pywebview.
Notas técnicas:
- `chat-downloader` está roto con YouTube actual: usar el descargador `live_chat` de yt-dlp (`youtube_chat.py`).
- pywebview: no guardar la ventana como atributo público del js_api (recursión infinita); usar `_window`.
- Huey: el consumidor corre en el mismo proceso; `_set_signal_handlers` anulado (no es el hilo principal).
- Al arrancar se vacía la cola Huey y se reencola según la DB (la DB manda).
- Segmentos del directo: tiempos reales desde `segments.csv` (con `-c copy` los cortes caen en keyframes).
Pruebas completas 2026-11-09: 41 tests, VOD 17 min -> 6 clips en 89 s, local 85 s, directo 336 s -> 4 clips (pico VRAM 6,2 GB), sincronía subtítulos ~10 ms, ZIP, móvil y ventana OK.
Pendiente: yt-dlp avisa de que falta runtime JS (deno); «Abrir ubicación…» nativo de la ventana sin probar con clic real.
- Live: streamlink con stream-segmented-queue-deadline=0 y reconexión; Whisper beam_size=5 (greedy perdía palabras); Ollama keep_alive acotado y liberado al arrancar.

## Empaquetado (2026-11-09)
- `installer\build.bat` -> PyInstaller (carpeta) + Inno Setup -> `build\installer\ClipStream-Setup-*.exe` (~160 MB).
- Congelado: código/assets de solo lectura en `sys._MEIPASS`; datos en `%LOCALAPPDATA%\ClipStream` (`config.settings.DATA_DIR`).
- CUDA, ffmpeg y modelos NO van en el instalador: `app/core/setup.py` los descarga en el primer arranque.
- El spec sustituye el runtime VC++ empaquetado (msvcp140 14.16 de algún paquete) por el del sistema: con el viejo,
  ctranslate2 revienta (access violation). No quitar ese bloque.
- `matplotlib` debe ir en el bundle: mediapipe lo importa al cargar.
- Kick: sin chat (decisión del usuario: el WebSocket interno no oficial queda fuera). VOD por HLS público, directo con streamlink.

## Fase 3 · microseries (2026-11-09)
- Voz: Kokoro ONNX en CPU (velocidad 0,92 ≈ 2,8 palabras/s). Tiempos por palabra con Whisper alineados al texto exacto del guion.
- Guion: el 7B escribe escenas cortas → segunda pasada «añade una o dos frases» (máx. 2) si < 165 palabras. Objetivo 60-90 s.
- Imágenes: SDXL base + Lightning 4 pasos fusionada, 768×1344. UNet+VAE fijos en GPU, codificadores de texto en CPU fp32
  (con enable_model_cpu_offload eran 15 s/imagen y picos de 7,9 GB; ahora 3,3 s y 6,3 GB reservados). Durante la difusión
  se apartan los codificadores (si no, la pipeline pasa los embeddings a fp32). Prompts largos troceados (prompt_embed.py).
- Guardián visual: CLIP L/14 en CPU, softmax entre famosos + genéricos + **señuelos** + descripción propia; bloquea si un
  famoso ≥ 0,45. Sin señuelos, cualquier pez era «Nemo» y cualquier barbudo «Moisés». 3 semillas antes de bloquear.
- Pendiente de empaquetar: torch no va en el .exe (excluido); descargarlo en el primer arranque como las DLL de CUDA.
- GPL-3.0: phonemizer/espeak-ng (ver installer/THIRD_PARTY.md).

- Montaje: Ken Burns con cv2.warpAffine subpíxel (zoompan tiembla) → NVENC; vídeo base común y una pasada por idioma
  (voz, música con ducking, whoosh en cortes, −14 LUFS, ASS + etiqueta «Contenido generado con IA»). 72 s ES+EN en ~38 s.
- Música: biblioteca vacía → bases propias CC0 por plantilla (app/shared/music/ambient.py, AAC ~1 MB). La serie puede elegir otra.
- Informe de derechos por episodio: epNN_derechos.txt/.json junto a los MP4 y la descripción epNN_<lang>.txt.
- Guion corto: 1.ª pasada «alarga todas» (una llamada) y, si sigue < 165 palabras, 2.ª escena a escena (24-32 palabras,
  sin diálogos nuevos entre comillas: en bíblicas el guardián los bloquea). Episodios reales: 72 s y 88 s.
- Pantalla Microseries: «Producir» encadena guion → imágenes → voz → montaje y se detiene ante un bloqueo; reescribir
  el guion reinicia las demás fases. La UI se actualiza por WebSocket (eventos «series» y «episode»).
- Instalador 0.4.0 (173 MB): el motor de imágenes (PyTorch cu128 + diffusers/transformers, 2,8 GB) NO va en el .exe;
  componente «engine» lo descarga como ruedas exactas (installer/pylib_manifest.json, generado en la compilación por
  installer/runtime_deps.py) a %LOCALAPPDATA%\ClipStream\runtime\pylib y se activa al final de sys.path (app/core/pylib.py).
  Paquetes compartidos con la app van ENTEROS en el .exe (collect_all + metadata); biblioteca estándar completa
  (torch importa pickletools, etc.). Con el motor instalado, Whisper usa el cuBLAS/cuDNN de torch/lib: no hace falta «cuda».
- OJO: la consola Bash de Claude ve una vista aislada de %LOCALAPPDATA%; para probar la versión instalada usar PowerShell.
- Prueba real empaquetada: descarga del motor ~2,5 min, serie + episodio de fábula ES/EN en 230 s.

## Rediseño UX y TikTok (2026-11-09)
- Microseries DESACTIVADAS a petición del usuario (`settings.stories_enabled=False`): sin pestaña, sin componentes de
  descarga y la API responde 404. El código sigue en app/stories y pages/Stories.tsx.
- Progreso por pasos (components/ProgressSteps.tsx) con `sources.stage_progress` (% de la fase actual).
- Clips: cola de render ordenada (pipeline._render_turn): uno cada vez, por fuente y segundo de inicio; la fuente
  muestra «Generando clip k/n» y el % total de la fase.
- Paneles ampliables: --side-w (separador lateral en Fuentes/Editor/Recorte/Exportar) y --monitor-h (bajo el monitor),
  guardados en localStorage; doble clic restablece. La tabla de clips usa container queries.
- TikTok (app/publish): Login Kit Desktop + PKCE (challenge en HEX, según docs), tokens y Client Secret cifrados con
  DPAPI (storage/tiktok.vault), Direct Post FILE_UPLOAD por trozos, estado por polling, límite 15/24 h, programación con
  hilo propio (cada 20 s). Ventana de publicar según Content Sharing Guidelines (ver docs/tiktok/GUIA_REVISION.md).
- Riesgo de revisión: TikTok no aprueba apps de uso personal ni que copien contenido de otras plataformas. Sin auditoría:
  posts privados (SELF_ONLY) y cuenta privada. Páginas legales en docs/site (rellenar [YOUR NAME]/[EMAIL]).
- Para probar sin chocar con la app instalada: servidor de desarrollo en otro puerto (CLIPSTREAM_PORT=8766).
- Efectos según el contenido (app/shared/fx/planner.py): visuales none/subtle/dynamic (por defecto dynamic) y sonidos
  none/auto/subtle/meme (por defecto auto). Umbrales YAMNet medidos: risas de conversación 0,15-0,3, aplausos 0,85+.
  Prioridades + separación mínima + tope por duración; con música o risas se reduce. Render: fx_chain (escala/recorte/brillo
  con expresiones por fotograma). 11 sonidos CC0 generados por código (scratch, fail, crickets, cash, boing nuevos).

## Pendiente al retomar (2026-11-09)
- TikTok: app «ClipStream» creada en developers.tiktok.com (Production, borrador). Hecho: icono, nombre, categoría
  Entertainment, descripción, explicación de revisión, Login Kit + Content Posting API (Direct Post activado),
  scopes user.info.basic + video.publish (+ video.upload incluido). Falta: marcar Desktop, Redirect URI en la pestaña
  Desktop de Login Kit, y las URLs https (web, términos, privacidad).
- Web: el usuario no tiene web. Plan: GitHub Pages (repo público `clipstream` solo con docs/site/*) o Netlify Drop.
  Antes hay que rellenar en docs/site [YOUR NAME], [YOUR CONTACT EMAIL], [COUNTRY], [DATE]: pedir los datos al usuario.
- Alternativa para probar sin web: pestaña Sandbox (no verificado si también exige la web).
- Luego: probar conexión real desde ClipStream (Exportar → TikTok), grabar vídeo de demostración y enviar a revisión.
- Instalador actual: build/installer/ClipStream-Setup-0.5.0.exe (efectos, pasos, TikTok). Sin probar: conexión real con
  TikTok y escuchar los 5 sonidos nuevos.
