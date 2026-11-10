# Componentes de terceros

ClipStream usa solo componentes con licencia que permite uso comercial. Lista de los relevantes
(las bibliotecas Python completas están en `pyproject.toml`; las licencias de música, SFX y textos en
`assets/licenses.json`).

## Modelos

| Componente | Licencia | Uso |
|---|---|---|
| Whisper large-v3-turbo (faster-whisper) | MIT | transcripción |
| Qwen2.5 7B Instruct (Ollama) | Apache-2.0 | guiones, títulos |
| MediaPipe (caras, pose, objetos, YAMNet, Face Landmarker) | Apache-2.0 | encuadre y señales |
| Kokoro-82M v1.0 (pesos) | Apache-2.0 | voz narradora de microseries |
| Stable Diffusion XL base 1.0 | CreativeML Open RAIL++-M | imágenes de microseries (uso comercial permitido; prohíbe usos dañinos, ver anexo A de la licencia) |
| SDXL-Lightning 4 pasos (LoRA, ByteDance) | CreativeML Open RAIL++-M | imágenes rápidas |
| SDXL VAE fp16-fix (madebyollin) | MIT | |
| CLIP ViT-L/14 (OpenAI) | MIT | guardián visual: parecido con personajes protegidos |

## Bibliotecas

| Componente | Licencia | Nota |
|---|---|---|
| kokoro-onnx | MIT | motor de voz |
| onnxruntime | MIT | |
| PyTorch | BSD-3 | |
| diffusers, transformers, accelerate, peft | Apache-2.0 | |
| faster-whisper / CTranslate2 | MIT | |
| FFmpeg (descargado en el primer arranque, build «essentials» de gyan.dev) | GPL-3.0 | programa aparte, se ejecuta como proceso |
| phonemizer / espeak-ng | GPL-3.0 | **No incluidos en el ejecutable publicado** (solo para la voz de las microseries, desactivadas) |

### Sobre los componentes GPL-3.0

El ejecutable publicado **no contiene componentes GPL**: el código de ClipStream es MIT y las bibliotecas
incluidas tienen licencias permisivas (MIT, BSD, Apache-2.0, OFL, CC0).

- **FFmpeg** (GPL-3.0) no va dentro del instalador: ClipStream lo descarga en el primer arranque (o usa el que ya
  tengas instalado) y lo ejecuta como un programa independiente. Código fuente: https://ffmpeg.org
- **phonemizer** y **espeak-ng** (GPL-3.0) solo los usa la voz de las microseries, que están desactivadas y no se
  incluyen en el ejecutable. Si alguien reactiva las microseries desde el código y distribuye el resultado,
  debe cumplir la GPL-3.0 (ofrecer el código fuente). Código: https://github.com/bootphon/phonemizer ·
  https://github.com/espeak-ng/espeak-ng

El contenido generado (vídeos, audios) no queda afectado por estas licencias.
