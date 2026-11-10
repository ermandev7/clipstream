"""Imágenes de escena con SDXL base 1.0 + SDXL-Lightning 4 pasos (Open RAIL++-M, uso comercial).

En 8 GB: UNet y VAE fp16 fijos en la GPU (~5,3 GB); los codificadores de texto en CPU (fp32), así no se
mueven 5 GB por PCIe en cada imagen (con enable_model_cpu_offload eran 15 s por imagen y picos de 7,9 GB).
Pasa por el gestor de GPU: nunca convive con Whisper ni con el LLM.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from config.settings import settings

from app.core.gpu import gpu

log = logging.getLogger(__name__)
SDXL_DIR = settings.models_dir / "sdxl-base"
VAE_DIR = settings.models_dir / "sdxl-vae-fp16-fix"
LIGHTNING = settings.models_dir / "sdxl-lightning" / "sdxl_lightning_4step_lora.safetensors"
WIDTH, HEIGHT = 768, 1344  # resolución nativa de SDXL más cercana a 9:16
STEPS = 4


def models_present() -> bool:
    return (SDXL_DIR / "unet" / "diffusion_pytorch_model.fp16.safetensors").exists() and LIGHTNING.exists() \
        and (VAE_DIR / "diffusion_pytorch_model.safetensors").exists()


def available() -> bool:
    """Modelos descargados y motor (PyTorch) disponible."""
    from app.core import pylib

    return models_present() and pylib.activate()


def _load():
    import torch
    from diffusers import AutoencoderKL, EulerDiscreteScheduler, StableDiffusionXLPipeline

    if not available():
        raise FileNotFoundError("Faltan el motor o los modelos de imagen: descárgalos en «Preparar ClipStream»")
    vae = AutoencoderKL.from_pretrained(str(VAE_DIR), torch_dtype=torch.float16)
    pipe = StableDiffusionXLPipeline.from_pretrained(str(SDXL_DIR), vae=vae, torch_dtype=torch.float16,
                                                     variant="fp16", use_safetensors=True)
    pipe.load_lora_weights(str(LIGHTNING.parent), weight_name=LIGHTNING.name)
    pipe.fuse_lora()
    pipe.unload_lora_weights()  # ya fusionada: libera las copias
    pipe.scheduler = EulerDiscreteScheduler.from_config(pipe.scheduler.config, timestep_spacing="trailing")
    pipe.text_encoder.to("cpu", torch.float32)
    pipe.text_encoder_2.to("cpu", torch.float32)
    pipe.unet.to("cuda")
    pipe.vae.to("cuda")
    pipe.vae.enable_tiling()  # diffusers 0.42: en el VAE, no en la pipeline
    pipe.set_progress_bar_config(disable=True)
    return pipe


def _unload(pipe) -> None:
    import torch

    pipe.unet.to("cpu")
    pipe.vae.to("cpu")
    del pipe
    torch.cuda.empty_cache()


gpu.register("sdxl", _load, _unload)


def seed_for(*parts) -> int:
    """Semilla estable: la misma escena da la misma imagen hasta que se pide otra."""
    return int(hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


def generate(prompt: str, out_png: Path, seed: int) -> Path:
    import torch

    from app.stories.prompt_embed import encode

    with gpu.use("sdxl") as pipe:
        embeds = encode(pipe, prompt)
        # Sin codificadores durante la difusión: si no, la pipeline pasa los embeddings a su tipo (fp32 en CPU).
        te1, te2 = pipe.text_encoder, pipe.text_encoder_2
        pipe.text_encoder = pipe.text_encoder_2 = None
        try:
            img = pipe(**embeds, num_inference_steps=STEPS, guidance_scale=0.0, width=WIDTH, height=HEIGHT,
                       generator=torch.Generator("cpu").manual_seed(seed)).images[0]
        finally:
            pipe.text_encoder, pipe.text_encoder_2 = te1, te2
    out_png.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_png)
    return out_png
