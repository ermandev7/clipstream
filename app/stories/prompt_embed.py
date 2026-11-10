"""Prompts largos para SDXL: CLIP corta a 77 tokens y se perderían el estilo o los personajes.

Se trocea en bloques de 75 tokens, se codifica cada uno y se concatenan (la UNet acepta
secuencias más largas en la atención cruzada). El vector «pooled» sale del primer bloque.
"""
from __future__ import annotations

CHUNK = 75
MAX_CHUNKS = 3


def chunks(tokenizer, prompt: str) -> list[str]:
    ids = tokenizer(prompt, add_special_tokens=False).input_ids
    parts = [ids[i:i + CHUNK] for i in range(0, len(ids), CHUNK)][:MAX_CHUNKS] or [[]]
    return [tokenizer.decode(p) for p in parts]


def encode(pipe, prompt: str) -> dict:
    import torch

    embeds, pooled = [], None
    for text in chunks(pipe.tokenizer, prompt):
        pe, _, pp, _ = pipe.encode_prompt(text, device=pipe.text_encoder.device, num_images_per_prompt=1,
                                          do_classifier_free_guidance=False)
        embeds.append(pe)
        pooled = pp if pooled is None else pooled
    dev, dtype = pipe.unet.device, pipe.unet.dtype  # codificadores en CPU, UNet en GPU
    return {"prompt_embeds": torch.cat(embeds, dim=1).to(dev, dtype), "pooled_prompt_embeds": pooled.to(dev, dtype)}
