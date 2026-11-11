# Componentes de terceros

ClipStream usa solo componentes con licencia que permite uso comercial. Lista de los relevantes
(las bibliotecas Python completas están en `pyproject.toml`; las licencias de música, SFX y textos en
`assets/licenses.json`).

## Modelos

| Componente | Licencia | Uso |
|---|---|---|
| Whisper large-v3-turbo (faster-whisper) | MIT | transcripción |
| Qwen2.5 7B Instruct (Ollama) | Apache-2.0 | evaluación de momentos, títulos |
| MediaPipe (caras, pose, objetos, YAMNet, Face Landmarker) | Apache-2.0 | encuadre y señales |

## Bibliotecas

| Componente | Licencia | Nota |
|---|---|---|
| onnxruntime | MIT | |
| faster-whisper / CTranslate2 | MIT | |
| hls.js | Apache-2.0 | reproductor del directo en el monitor |
| FFmpeg (descargado en el primer arranque, build «essentials» de gyan.dev) | GPL-3.0 | programa aparte, se ejecuta como proceso |

### Sobre los componentes GPL-3.0

El ejecutable publicado **no contiene componentes GPL**: el código de ClipStream es MIT y las bibliotecas
incluidas tienen licencias permisivas (MIT, BSD, Apache-2.0, OFL, CC0).

- **FFmpeg** (GPL-3.0) no va dentro del instalador: ClipStream lo descarga en el primer arranque (o usa el que ya
  tengas instalado) y lo ejecuta como un programa independiente. Código fuente: https://ffmpeg.org

El contenido generado (vídeos, audios) no queda afectado por estas licencias.
