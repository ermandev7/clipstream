# ClipStream — Prompt y especificación para Claude Code

> **Cómo usar este archivo:** guárdalo en una carpeta vacía (por ejemplo `C:\Proyectos\clipstream`), abre Claude Code en esa carpeta y escribe:
> *"Lee CLIPSTREAM_PROMPT.md y empieza."*

---

## 0. Instrucciones para Claude Code

Eres el desarrollador principal de **ClipStream**, una aplicación de escritorio para Windows. Este documento contiene todas las decisiones ya tomadas con el usuario. **No las vuelvas a discutir**; si algo es técnicamente imposible, avisa y propone la alternativa más cercana.

**Tu primera tarea:**
1. Lee este documento completo.
2. Crea `CLAUDE.md` en la raíz con un resumen de las reglas del proyecto (sección 2 y sección 9) para tenerlas presentes en futuras sesiones.
3. Comprueba el entorno del usuario: versión de Windows, Python, driver NVIDIA y CUDA (`nvidia-smi`), si existe `ffmpeg` y si NVENC está disponible. Informa de lo que falte antes de instalar nada.
4. Ejecuta **solo la Fase 1** (sección 10). Al terminar cada tarea, pruébala de verdad en la máquina del usuario.
5. Habla con el usuario **en español**.

**Reglas de trabajo:**
- Trabaja en pasos pequeños y verificables. Ejecuta y prueba cada pieza antes de pasar a la siguiente.
- Antes de descargar modelos grandes (más de 1 GB), dile al usuario qué vas a descargar y cuánto ocupa.
- Usa `git` desde el inicio, con commits pequeños y descriptivos.
- Escribe tests para la lógica (detección, cortes, subtítulos). Para lo que depende de GPU o red, haz pruebas manuales con un vídeo corto.
- Antes de añadir cualquier dependencia o modelo, **verifica su licencia actual** en su página oficial (sección 6).

---

## 1. Qué es ClipStream

Aplicación local que genera vídeos verticales listos para TikTok, Instagram Reels, Facebook y YouTube Shorts. Tiene dos "estudios":

1. **Clipper:** recibe un vídeo y saca automáticamente **clips verticales de 3 segundos a 3 minutos** con potencial viral. Cada clip sale con subtítulos, título, hashtags, portada y efectos de sonido.
2. **Microseries (fase posterior):** crea series cortas con IA (historias bíblicas, mitología, fábulas, leyendas, "frutinovelas") usando solo historias de dominio público.

### Tres tipos de fuente para el clipper
| Fuente | Entrada | Comportamiento |
|---|---|---|
| **Directo** | Link de un streaming en vivo de YouTube o Kick | Captura continua con buffer circular; los clips aparecen en tiempo real (30–90 s después del momento) |
| **Vídeo publicado** | Link de un vídeo o directo ya terminado de YouTube o Kick | Descarga y analiza el vídeo completo. Si fue un directo, usa también la repetición del chat |
| **Archivo local** | Botón **"Abrir ubicación…"** (diálogo de Windows para elegir MP4/MOV/MKV) | Analiza el archivo completo; sin señal de chat |

### Salida
- Descarga manual (MP4 + portada PNG + título/descripción/hashtags .txt + opcional .srt + informe de derechos).
- **Más adelante (Fase 5):** publicación directa por las APIs oficiales. Deben existir **las dos opciones**.

---

## 2. Decisiones fijadas

| Tema | Decisión |
|---|---|
| Usuario | Una sola persona. Monetiza sus propios canales. **La app no se vende**: sin cuentas, sin pagos, sin servidor |
| Sistema | Windows |
| GPU | **NVIDIA RTX 3060 con 8 GB de VRAM.** La "memoria compartida" no cuenta. Planifica todo para 8 GB |
| Idiomas | Español + inglés (subtítulos, títulos, voces) |
| Lenguaje | **Python 3.11+** para el motor |
| Recursos | **Solo gratuitos y con licencia de uso comercial** (ver sección 6) |
| Derechos — clips | **Solo avisar, nunca bloquear.** El clip se genera siempre y se muestra un aviso |
| Derechos — microseries | **Avisar y bloquear.** Si se detecta copyright, no se genera hasta corregirlo |
| Distribución | Instalador **.exe** para Windows; la interfaz se abre en una ventana propia |
| Acceso extra | Opción para abrir la interfaz desde el móvil en la misma WiFi |

---

## 3. Stack técnico

- **Backend:** Python 3.11+, FastAPI, WebSocket para actualizaciones en vivo.
- **Base de datos:** SQLite (un archivo, sin servidor).
- **Cola de trabajos:** Huey con backend SQLite. **No usar Redis** (problemas en Windows).
- **Sin Docker.** Python nativo en Windows con CUDA.
- **Vídeo:** ffmpeg (codificación con NVENC), streamlink, yt-dlp.
- **Frontend:** React + Vite (TypeScript).
- **Empaquetado:** PyInstaller (motor) + pywebview (ventana propia) + Inno Setup (instalador). Los modelos de IA **no** van en el .exe: se descargan la primera vez con barra de progreso.

### Presupuesto de VRAM (8 GB)
| Tarea | Modelo | VRAM aprox. |
|---|---|---|
| Transcripción | faster-whisper `large-v3-turbo` o `medium`, int8 | 2–3 GB |
| LLM | Qwen2.5-7B-Instruct o Mistral-7B-Instruct, Q4, vía Ollama | ~5 GB |
| Caras | mediapipe | CPU |
| Render | ffmpeg NVENC | mínima |

Whisper y el LLM **no caben juntos cómodamente**: el sistema los carga por turnos. El LLM solo evalúa candidatos, no todo el stream. Implementa un **gestor de GPU** que serialice las tareas pesadas.

---

## 4. Arquitectura y pipeline del clipper

```
FUENTE (directo / vídeo publicado / archivo local)
  │
1. INGESTA ── streamlink / yt-dlp / archivo → ffmpeg → segmentos de 10 s
  │            Directo: buffer circular de ~15 min
  │
2. SEÑALES (en paralelo)
  ├─ Transcripción con tiempos por palabra (faster-whisper)
  ├─ Audio: picos de volumen, risas, gritos
  ├─ Chat: mensajes/s, emotes, "JAJA", "clip" (si hay chat o repetición)
  ├─ Eventos: donaciones, subs, raids, picos de espectadores
  └─ Vídeo: cambios de escena
  │
3. DETECCIÓN
  ├─ scorer: combina señales baratas → candidatos
  ├─ llm_judge: el LLM evalúa el texto del candidato (gancho, se entiende solo,
  │             emoción) → puntuación 0-100 + explicación breve
  ├─ boundaries: ajusta inicio/fin a frases completas (3 s – 3 min)
  └─ dedup: fusiona candidatos que se solapan
  │
4. RENDER
  ├─ Reencuadre 9:16 (ver sección 5)
  ├─ Gancho: texto superior en los 2 primeros segundos + zoom en el pico
  ├─ Subtítulos palabra por palabra (ASS + libass), palabras clave resaltadas
  ├─ Efectos de sonido (ninguno / sutil / meme) + ducking de la voz
  └─ Normalización de audio a −14 LUFS
  │
5. METADATOS ── título, descripción y hashtags en ES y EN por plataforma
6. PORTADA ──── mejor frame (nitidez, cara, expresión) + texto
7. DERECHOS ─── avisos (música detectada, permiso del streamer no marcado)
8. UI ───────── el clip aparece en la tabla vía WebSocket
```

En **vídeo publicado** y **archivo local**, se analiza el vídeo entero y se eligen los mejores momentos comparándolos entre sí (no solo los que superan un umbral).

---

## 5. Reencuadre vertical

**Una persona:** detección de cara/cuerpo (mediapipe) en cada frame → ventana 9:16 centrada → movimiento **suavizado** con zona muerta (la cámara no se mueve con movimientos pequeños; sigue con movimiento lento si la persona se desplaza). Si sale de cuadro → centro o fondo desenfocado.

**Facecam + juego:** detectar una vez la región fija de la facecam y usar el diseño facecam arriba, juego abajo.

**Dos o tres personas:**
| Modo | Uso |
|---|---|
| Hablante activo | Corta o se desplaza a quien habla (movimiento de labios + audio; TalkNet o Light-ASD) |
| Dividido ×2 / ×3 | Personas apiladas en franjas |
| Encuadre de grupo | Un recorte que incluye a todos, si caben |
| Plano general | Imagen completa con fondo desenfocado arriba y abajo |

**Automático:** si caben todos → grupo; si hablan por turnos → hablante activo; si hablan a la vez → dividido.
**Manual:** el usuario dibuja cajas por persona sobre un frame y elige diseño. Se guarda como **preset del directo** y se aplica a todos sus clips. Cada clip puede cambiar de diseño antes de exportar.

---

## 6. Recursos libres (uso comercial)

### Usar ✅
| Componente | Recurso |
|---|---|
| Captura | streamlink, yt-dlp, ffmpeg |
| Chat | `chat-downloader` (YouTube). Kick: investigar primero la API pública oficial; si no cubre el chat, websocket |
| Transcripción | faster-whisper (MIT) |
| Subtítulos | ASS + libass; fuentes de Google Fonts (OFL): Montserrat, Anton, Bebas Neue, Poppins |
| Caras | mediapipe (Apache 2.0) |
| Hablante activo | TalkNet / Light-ASD (verificar licencia) |
| Detección de música | inaSpeechSegmenter o YAMNet (detectan *que hay* música) |
| LLM | Ollama + Qwen2.5-7B-Instruct o Mistral-7B-Instruct |
| Imágenes (microseries) | SDXL; FLUX.1 schnell (Apache 2.0) cuantizado |
| Imagen → vídeo | Wan 2.1 1.3B (Apache 2.0) |
| Voces | Kokoro (Apache 2.0, ES + EN), Piper (MIT) |
| Labios | LatentSync (verificar licencia) |
| Música | Pixabay Music, YouTube Audio Library, Free Music Archive (CC0) |
| Efectos | Freesound (solo CC0), Pixabay SFX |

### Evitar ❌
- YOLO de Ultralytics (AGPL).
- FLUX.1 dev (no comercial).
- HunyuanVideo (su licencia excluye la UE; el usuario está en España).
- XTTS-v2 y pesos de F5-TTS (no comerciales).
- Wav2Lip (no comercial).
- MusicGen (pesos no comerciales).
- ACRCloud / AudD (de pago).
- Redis (usar la cola con SQLite).

### Control de licencias en el código
- `assets/licenses.json` registra la licencia de cada archivo de `assets/`.
- `config/settings.py`:
  ```python
  ALLOWED_LICENSES = ["CC0", "CC-BY", "Apache-2.0", "MIT", "OFL", "Pixabay", "YouTube-AudioLibrary"]
  RIGHTS_POLICY = {"stories": "block", "clipper": "warn"}
  ```
- Un archivo sin licencia permitida **no se usa**. Los modelos permitidos están en una lista blanca.
- CC-BY: añadir el crédito automáticamente en la descripción.

---

## 7. Derechos

### Clips (política `warn`)
- Detectar música en el audio → aviso con el rango de tiempo ("Música detectada 0:12–0:40").
- Campo "permiso del streamer" por canal (sí / no / programa de clips) → aviso si no está marcado.
- **Nunca bloquear ni modificar** el clip por estos motivos.

### Microseries (política `block`)
| Paso | Comprobación |
|---|---|
| Historia | ¿Está en el catálogo de dominio público? Si no, el LLM analiza y avisa |
| Guion | ¿Menciona franquicias o personajes protegidos? ¿Copia letras de canciones? |
| Citas bíblicas | Solo textuales de la **Reina-Valera 1909**; el resto parafraseado |
| Imágenes | Revisión con modelo de visión: ¿se parece a un personaje famoso? |
| Audio | Solo archivos de la biblioteca con licencia registrada |

Si falla: se bloquea, se explica qué y dónde, y se propone una alternativa.
Cada vídeo genera un **informe de derechos** (fuentes y licencias usadas).
Al publicar microseries: marcar como **contenido generado/alterado con IA**.

---

## 8. Estructura del proyecto

```
clipstream/
├── CLAUDE.md
├── README.md
├── pyproject.toml
├── .env.example
├── config/
│   ├── settings.py              # incluye RIGHTS_POLICY y ALLOWED_LICENSES
│   └── presets/
│       ├── subtitles/           # estilos .ass
│       ├── covers/
│       └── layouts.json
├── assets/
│   ├── sfx/  music/  fonts/
│   └── licenses.json
├── app/
│   ├── main.py                  # FastAPI + arranque
│   ├── desktop.py               # ventana pywebview
│   ├── api/
│   │   ├── sources.py           # crear/iniciar/detener fuentes (directo, publicado, local)
│   │   ├── clips.py
│   │   ├── stories.py
│   │   ├── publish.py
│   │   └── ws.py
│   ├── core/
│   │   ├── models.py            # Source, Clip, Series, Episode, Character, Scene, PublishJob
│   │   ├── db.py
│   │   ├── events.py
│   │   ├── queue.py             # Huey + SQLite
│   │   └── gpu.py               # gestor de VRAM: serializa tareas pesadas
│   ├── shared/
│   │   ├── llm/                 # cliente Ollama + prompts
│   │   ├── transcription/
│   │   ├── subtitles/
│   │   ├── sfx/
│   │   ├── music/
│   │   ├── hook/
│   │   ├── metadata/
│   │   ├── thumbnail/
│   │   ├── render/              # ffmpeg + NVENC
│   │   ├── rights/
│   │   │   ├── catalog.py  text_guard.py  visual_guard.py
│   │   │   ├── audio_guard.py  permissions.py  policy.py  report.py
│   │   ├── notify/
│   │   └── publish/             # Fase 5
│   │       ├── base.py  tiktok.py  instagram.py  facebook.py  youtube.py  scheduler.py
│   ├── clipper/
│   │   ├── ingest/
│   │   │   ├── base.py          # interfaz StreamSource
│   │   │   ├── youtube_live.py
│   │   │   ├── kick_live.py
│   │   │   ├── vod.py           # vídeo publicado (YouTube/Kick)
│   │   │   ├── local_file.py
│   │   │   └── buffer.py
│   │   ├── signals/
│   │   │   ├── audio_peaks.py  laughter.py  scene.py  viewers.py
│   │   │   └── chat/  youtube_chat.py  kick_chat.py  events.py
│   │   ├── detection/
│   │   │   ├── scorer.py  llm_judge.py  boundaries.py  dedup.py
│   │   ├── reframe/
│   │   │   ├── detector.py  tracker.py  speaker.py  layouts.py  planner.py  presets.py
│   │   └── pipeline.py
│   ├── stories/                 # Fases 3-4
│   │   ├── script/  characters/  scenes/  animate/  voices/  modes/
│   │   ├── templates/  frutinovela/  biblicas/  mitologia/  fabulas/  leyendas/
│   │   ├── assemble.py
│   │   └── pipeline.py
│   ├── analytics/               # Fase 5
│   └── workers/
├── frontend/
│   └── src/
│       ├── pages/  Sources.tsx  ClipEditor.tsx  CropEditor.tsx  StoryStudio.tsx  Publish.tsx
│       └── components/  RightsBadge.tsx  Timeline.tsx  ...
├── installer/                   # PyInstaller spec + Inno Setup
├── storage/                     # fuera de git
└── tests/
```

---

## 9. Diseño de la interfaz (mockups aprobados)

### Estilo general
Herramienta de edición **profesional**, tipo DaVinci Resolve o Premiere. **Nada que parezca "hecho con IA"**: sin degradados, sin tarjetas muy redondeadas, sin emojis, sin etiquetas de colores chillones.

**Tokens:**
| Token | Valor |
|---|---|
| Fondo | `#141415` |
| Panel / cabecera | `#1A1A1C` |
| Control | `#1F1F22` |
| Borde | `#2C2C2F` (líneas), `#36363A` (controles) |
| Texto | `#E6E6E7` |
| Texto secundario | `#A1A1A6` |
| Acento (selección, botón principal) | `#4C8DFF`; segmento activo `#2A3A57` |
| En vivo / bloqueo | `#E5484D` / texto `#F2777B` |
| Aviso | `#D9A441` / texto `#E0B861` |
| Correcto | `#8FD19E` |
| Fuentes | **IBM Plex Sans** (interfaz), **IBM Plex Mono** (tiempos, datos técnicos) |
| Radio | 4 px en controles; paneles sin radio, separados por líneas de 1 px |
| Tamaño base | 13 px; etiquetas de sección 11 px en mayúsculas con espaciado |

**Patrones:** tablas compactas, inspectores laterales con filas etiqueta/valor, controles segmentados, pestañas superiores con subrayado del acento. Todo debe funcionar también en pantalla de móvil (los paneles se apilan).

**Cabecera común (48 px):** "ClipStream" a la izquierda · pestañas **Fuentes | Editor | Recorte | Microseries | Exportar** · a la derecha, en mono: "RTX 3060 · VRAM 6.1 / 8.0 GB" (dato real del gestor de GPU).

### Pantalla 1 — Fuentes
- **Barra de fuente:** etiqueta "FUENTE" + control segmentado **Directo | Vídeo publicado | Archivo local**.
  - Directo / Vídeo publicado: selector de plataforma (YouTube, Kick) + campo de link (mono) + botón ("Detener captura" en directo, "Analizar" en publicado).
  - Archivo local: campo de ruta de solo lectura + botón **"Abrir ubicación…"** + botón principal "Analizar".
- **Monitor (izquierda):** estado (punto rojo "EN VIVO", "ANALIZANDO 64 %" o "ARCHIVO LOCAL"), nombre de canal/título o archivo, timecode en mono, monitor 16:9.
- **Barra de actividad:** histograma de actividad del buffer (directo) o del vídeo completo, con los clips detectados marcados como rangos en el color de acento.
- **Tabla "Clips detectados":** miniatura 9:16 pequeña · título · puntuación (número + barra fina) · entrada · duración · señales ("Chat ×8, risa, gancho") · estado (Listo en verde, "Aviso · música" en ámbar, "Renderizando 64 %" en gris) · botón "Abrir".
- **Panel derecho:** "SEÑALES" con mini-histogramas (mensajes de chat, nivel de audio en LUFS, risas, espectadores; en archivo local el chat aparece como "No disponible") y "REGISTRO" con eventos con hora en mono.

### Pantalla 2 — Editor de clip
- Barra superior: título del clip, entrada → salida · duración · 1080×1920 en mono, botones "Deshacer" y "Exportar".
- **Visor 9:16** centrado sobre fondo casi negro, con la **zona segura** de TikTok/Reels en línea discontinua, texto gancho arriba y subtítulo palabra por palabra (palabra activa en amarillo `#F5D547`). Controles de reproducción y timecodes.
- **Inspector derecho:**
  - Encuadre: segmentado de 6 opciones (Seguir cara, Facecam + juego, Desenfocado, Dividido ×2, Dividido ×3, Quien habla) + enlace "Editar recorte manual".
  - Subtítulos: estilo, idioma (ES, EN, ES + EN), texto gancho.
  - Audio: efectos (Ninguno / Sutil / Meme), música (solo con licencia), normalización −14 LUFS.
  - Metadatos: título ES, título EN, hashtags.
  - Aviso de derechos en ámbar (sin bloquear).
- **Timeline inferior** con regla de tiempos y pistas: V1 Vídeo · T1 Gancho · T2 Subtítulos · A1 Voz · A2 Efectos · A3 Música, bloques editables y cabezal de reproducción rojo.

### Pantalla 3 — Recorte
- Barra: título "Recorte vertical · preset del directo", segmentado **Automático | Manual**, casilla "Aplicar a todos los clips de este directo".
- **Fuente 16:9** con una caja de color por persona detectada (A azul, B ámbar, C verde), arrastrables y redimensionables.
- **Tabla de personas:** confianza, tiempo hablando, posición en el vertical, casilla "Incluir".
- **Panel derecho:** miniaturas esquemáticas de los 6 diseños, controles de seguimiento (suavizado, zona muerta, cambio de plano: corte seco o desplazamiento suave) y vista previa de la salida 9:16.

### Pantalla 4 — Microseries
- **Lista de series** a la izquierda (nombre + plantilla + nº de episodios), botón "Nueva".
- **Cabecera de serie:** nombre, plantilla, modo (Narrado / Dramatizado), idiomas, fuente de dominio público. Botón "Generar episodio".
- Pestañas de episodios.
- **Progreso por fases:** Guion · Personajes · Imágenes · Voz · Montaje, con estado de cada una.
- **Storyboard:** cuadrícula de escenas (miniatura, número, duración, texto de narración, estado: Aprobada / Revisar / Generando / Pendiente / **Bloqueada** con rayado rojo).
- **Panel derecho:** "Guardián de derechos · bloqueo activo" con la lista de comprobaciones, caja roja de la escena bloqueada (motivo + "Regenerar" / "Ver detalle") y la lista de personajes con su voz o referencia visual.

### Pantalla 5 — Exportar
- Barra: "Cola de exportación", nº seleccionados, botones "Descargar ZIP" y "Publicar seleccionados".
- **Tabla:** casilla · vídeo (miniatura, título, tipo y duración) · idioma · destinos (TikTok, Reels, Facebook, YouTube, Shorts) · programado · estado.
- **Panel derecho:** formato (1080×1920, H.264 NVENC, 12 Mbps, AAC 192k −14 LUFS), casillas de qué incluir en la descarga, y cuentas para publicar (YouTube, TikTok, Instagram Reels, Facebook) con su estado y botón "Conectar"/"Gestionar". En la Fase 1 esta sección se muestra deshabilitada con la nota "Disponible en una versión posterior".

---

## 10. Roadmap

### Fase 1 — MVP del clipper (EMPIEZA AQUÍ)
**Objetivo:** con cualquiera de las tres fuentes, obtener clips verticales con subtítulos en español y descargarlos desde la interfaz.

Tareas:
1. Esqueleto del proyecto (estructura de la sección 8, solo lo necesario), `pyproject.toml`, git, `CLAUDE.md`.
2. Comprobación de entorno y descarga de modelos con progreso (faster-whisper; Ollama + Qwen2.5-7B Q4).
3. `core/gpu.py`: gestor que carga y descarga Whisper y el LLM por turnos.
4. Ingesta de las tres fuentes: directo de YouTube con buffer circular, vídeo publicado de YouTube y archivo local.
5. Señales: transcripción con tiempos por palabra + picos de audio + chat de YouTube (en directo y repetición).
6. Detección: scorer + llm_judge + boundaries + dedup.
7. Render: reencuadre 9:16 con **fondo desenfocado** (sin seguimiento de cara todavía) + subtítulos ES palabra por palabra + NVENC.
8. API FastAPI + WebSocket.
9. Frontend: pantallas **Fuentes** (completa) y **Exportar** (solo descarga), con el estilo de la sección 9.
10. Ventana de escritorio con pywebview.

**Criterios de aceptación:**
- Pegar un link de un vídeo de YouTube de 10–20 min produce al menos 3 clips con subtítulos sincronizados, en menos tiempo que la duración del vídeo.
- Un archivo local elegido con "Abrir ubicación…" produce clips igual.
- Un directo de YouTube genera clips mientras se emite, sin que la VRAM pase de 8 GB.
- Los clips se descargan como MP4 1080×1920 + .txt con el título.

### Fase 2 — Clipper completo
Kick (directo y publicado) · eventos de chat (donaciones, subs, raids) · seguimiento de cara · facecam + juego · hablante activo · editor de recorte manual con presets · gancho y zooms · efectos de sonido · título, hashtags y portada con IA · subtítulos EN · avisos de música y permiso · reprocesado del directo al terminar · pantallas Editor y Recorte.

### Fase 3 — Microseries, modo narrado
Catálogo de dominio público · guardián de derechos con bloqueo · guion por episodios con continuidad · biblia de personajes · imágenes SDXL · voz narradora Kokoro ES/EN · música, efectos y subtítulos · montaje con movimiento de cámara (Ken Burns) · plantillas bíblicas, mitología, fábulas, leyendas y frutinovela · pantalla Microseries.

### Fase 4 — Animación y dramatizado
Escenas animadas con Wan 2.1 1.3B · voces distintas por personaje · opción de GPU alquilada para labios y animación de más calidad.

### Fase 5 — Publicación y aprendizaje
APIs de YouTube, TikTok, Instagram y Facebook (manteniendo la descarga manual) · programación y títulos A/B · etiqueta de contenido IA automática · analítica para ajustar la detección · empaquetado final con instalador .exe.

---

## 11. Notas comerciales para el usuario

- **YouTube:** clips de canales ajenos pueden ser rechazados para monetización como "contenido reutilizado". Las microseries con IA deben marcarse como contenido sintético y tener estilo propio.
- **APIs:** registrar las apps de TikTok, Meta y Google pronto; la revisión tarda. Hasta que TikTok apruebe la app, las publicaciones por API suelen quedar privadas.
- **Permisos:** guardar por escrito el permiso de los streamers con los que se trabaje.
- Las licencias de los modelos cambian: verificar antes de cada lanzamiento.
