"""Guardián visual: ¿la imagen generada se parece a un personaje con derechos?

CLIP ViT-L/14 (MIT) en CPU, clasificación sin entrenamiento: se compara la imagen con su propia
descripción y con personajes famosos de adaptaciones modernas. Si un personaje famoso gana con
claridad, la escena se bloquea (política de microseries: avisar y bloquear) y se propone regenerar.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from config.settings import settings

log = logging.getLogger(__name__)
CLIP_DIR = settings.models_dir / "clip-vit-large-patch14"
_lock = threading.Lock()

# Personajes de adaptaciones con derechos cercanas a las historias del catálogo, y franquicias muy conocidas.
FAMOUS = [
    "Simba from Disney's The Lion King", "Mufasa from Disney's The Lion King", "the cartoon Hercules from Disney's 1997 animated film",
    "the cartoon Hades from Disney's Hercules", "the cartoon Zeus from Disney's Hercules", "the animated Moana from Disney", "the animated Maui from Disney's Moana",
    "Disney's Pinocchio", "Jiminy Cricket from Disney", "Snow White from Disney", "Cinderella from Disney",
    "Ariel from Disney's The Little Mermaid", "the Genie from Disney's Aladdin", "the cartoon Aladdin from Disney",
    "the cartoon Mulan from Disney", "Robin Hood fox from Disney", "the animated Moses from DreamWorks' The Prince of Egypt",
    "Shrek from DreamWorks", "Po from Kung Fu Panda", "Mickey Mouse", "Donald Duck", "Winnie the Pooh from Disney",
    "Bambi from Disney", "Dumbo from Disney", "Woody from Toy Story", "Nemo from Pixar's Finding Nemo",
    "a Minion from Despicable Me", "Thor from Marvel", "Loki from Marvel", "Wonder Woman from DC",
    "Spider-Man", "Superman", "Batman", "Pikachu from Pokemon", "Super Mario", "Sonic the Hedgehog",
    "Kratos from God of War", "Larry the Cucumber from VeggieTales", "Bob the Tomato from VeggieTales",
    "Bugs Bunny", "Tom and Jerry", "Peppa Pig", "SpongeBob SquarePants", "Elsa from Disney's Frozen",
]
GENERIC = ["an original digital painting", "an original storybook illustration", "a cinematic concept art scene"]
# Señuelos: el concepto genérico detrás de cada personaje. Sin ellos CLIP llama «Nemo» a cualquier pez,
# «Moisés» a cualquier barbudo con túnica o «Hércules» a cualquier hombre musculoso.
DECOYS = [
    "a lion cub", "a lion", "a fish", "a clownfish", "a whale", "a muscular shirtless man", "a bearded man in ancient robes",
    "an old bearded god with a white beard", "a young woman", "a polynesian girl", "a princess in a dress", "a mermaid",
    "a mouse", "a duck", "a bear", "a deer", "an elephant", "a wooden puppet", "a cricket", "a fox", "a panda", "an ogre",
    "a toy cowboy", "a superhero in a costume", "a muscular warrior with an axe", "a cucumber", "a tomato", "a rabbit",
    "a pig", "a sea sponge", "a queen with an ice dress", "a blue genie", "a prophet", "sailors on a ship",
    "a man sleeping", "a stormy sea", "a sea monster", "a woman warrior", "a hedgehog", "a man with a mustache",
    "a yellow creature", "a fruit with a face", "a greek god", "a hero with a sword", "an oil painting of a man",
    "a man in stormy ocean waves", "a man holding a spear", "a tattooed islander", "a man inside a giant fish",
]
MARGIN = 0.0  # el famoso debe superar a la propia descripción de la escena
MIN_PROB = 0.45  # y concentrar esta probabilidad frente a todo lo demás


@dataclass
class ImageCheck:
    ok: bool
    message: str
    match: str = ""
    prob: float = 0.0
    suggestion: str = ""


def available() -> bool:
    return (CLIP_DIR / "model.safetensors").exists()


def _unit(x):
    """transformers 5 devuelve un objeto con `pooler_output` (ya proyectado); la 4, el tensor."""
    x = getattr(x, "pooler_output", x)
    return x / x.norm(dim=-1, keepdim=True)


@cache
def _model():
    from transformers import CLIPModel, CLIPProcessor

    model = CLIPModel.from_pretrained(str(CLIP_DIR)).eval()
    return model, CLIPProcessor.from_pretrained(str(CLIP_DIR))


@cache
def _famous_features():
    import torch

    model, proc = _model()
    with torch.no_grad():
        return _unit(model.get_text_features(**proc(text=[f"a picture of {x}" for x in FAMOUS] + GENERIC + DECOYS,
                                                    return_tensors="pt", padding=True, truncation=True)))


def check_image(image_path: Path, description: str) -> ImageCheck:
    """`description` es la escena en pocas palabras (se usa como hipótesis propia)."""
    if not available():
        return ImageCheck(True, "Revisión visual no disponible (falta CLIP): revisa la imagen a mano")
    import torch
    from PIL import Image

    with _lock, torch.no_grad():
        model, proc = _model()
        img = _unit(model.get_image_features(**proc(images=Image.open(image_path).convert("RGB"), return_tensors="pt")))
        own = _unit(model.get_text_features(**proc(text=[description], return_tensors="pt", padding=True, truncation=True)))
        texts = torch.cat([_famous_features(), own])
        probs = (100 * img @ texts.T).softmax(dim=-1)[0]
    n = len(FAMOUS)
    best = int(probs[:n].argmax())
    p_best, p_own = float(probs[best]), float(probs[-1])
    if p_best >= MIN_PROB and p_best > p_own + MARGIN:
        return ImageCheck(False, f"La imagen se parece a «{FAMOUS[best]}» ({p_best:.0%})", FAMOUS[best], round(p_best, 3),
                          "Regenera la imagen (otra semilla) o cambia la descripción visual del personaje")
    return ImageCheck(True, "Sin parecidos con personajes protegidos", FAMOUS[best], round(p_best, 3))


# ---- Fidelidad: ¿sale en la imagen lo que dice la escena? --------------------------------------
# No es una cuestión de derechos sino de calidad: si falla, la escena pasa a «Revisar», nunca se bloquea.
KIND_DECOYS = [
    "lion", "mouse", "fox", "dog", "cat", "wolf", "bear", "bird", "horse", "rabbit", "tortoise", "hare", "fish", "whale",
    "snake", "frog", "monkey", "elephant", "deer", "goat", "sheep", "cow", "pig", "rooster", "crow", "ant", "grasshopper",
    "man", "woman", "boy", "girl", "old man", "old woman", "king", "soldier", "crowd of people", "ship", "empty landscape",
    "building", "tree", "fruit",
]
MIN_SUBJECT = 0.4


@dataclass
class SubjectCheck:
    ok: bool
    message: str
    score: float = 0.0
    top: str = ""


def _overlap(a: str, b: str) -> bool:
    wa, wb = set(a.lower().split()), set(b.lower().split())
    return bool(wa & wb)


def check_subject(image_path: Path, kinds: list[str]) -> SubjectCheck:
    """Compara lo que debería verse (los tipos de personaje de la escena) con alternativas parecidas.
    Correcta si gana uno de los esperados o si entre todos suman ≥ 40 %."""
    kinds = [k for k in dict.fromkeys(k.strip().lower() for k in kinds) if k]
    if not kinds or not available():
        return SubjectCheck(True, "Sin comprobación de contenido")
    import torch
    from PIL import Image

    decoys = [d for d in KIND_DECOYS if not any(_overlap(d, k) for k in kinds)]
    labels = kinds + decoys
    with _lock, torch.no_grad():
        model, proc = _model()
        img = _unit(model.get_image_features(**proc(images=Image.open(image_path).convert("RGB"), return_tensors="pt")))
        txt = _unit(model.get_text_features(**proc(text=[f"a picture of a {x}" for x in labels], return_tensors="pt",
                                                   padding=True, truncation=True)))
        probs = (100 * img @ txt.T).softmax(dim=-1)[0]
    top = labels[int(probs.argmax())]
    score = float(probs[: len(kinds)].sum())
    main = float(probs[0])  # el protagonista de la escena tiene que verse, no basta con el secundario
    if (top in kinds or score >= MIN_SUBJECT) and (main >= 0.1 or top == kinds[0]):
        return SubjectCheck(True, f"Se ve {top} ({score:.0%})", round(score, 3), top)
    if top in kinds or score >= MIN_SUBJECT:
        return SubjectCheck(False, f"No se ve al protagonista ({kinds[0]}): domina {top}", round(score, 3), top)
    return SubjectCheck(False, f"La imagen no muestra {' ni '.join(kinds)}: parece {top}", round(score, 3), top)
