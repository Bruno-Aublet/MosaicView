"""Génère les jauges de l'info-bulle de l'indicateur de mémoire (barre de
statut) : le cadran `icons/Memory_Gauge_00.png` avec l'aiguille
`icons/Memory_Gauge_00_needle.png` tournée selon le pourcentage, une image
par pourcentage entier de 0 à 100, dans le dossier `memory_gauge/` de la
racine (hors de `icons/`, dont le contenu est exporté avec les icônes).

Deux tailles par pourcentage : `gauge_NNN.png` (48 px, taille affichée) et
`gauge_NNN@2x.png` (96 px, écran à haute densité). Le texte riche de Qt
réduit une image sans lissage : elles sont livrées déjà réduites, ici avec
lissage, à partir de l'assemblage en pleine résolution.

Usage, depuis la racine du dépôt :
    python tools/make_memory_gauges.py                  # les 202 images
    python tools/make_memory_gauges.py --apercu DOSSIER # planche de contrôle

À relancer après toute retouche du cadran ou de l'aiguille ; les mesures
ci-dessous sont à reprendre si leur dessin change de place.
"""
import os
import sys

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIAL = os.path.join(ROOT, "icons", "Memory_Gauge_00.png")
NEEDLE = os.path.join(ROOT, "icons", "Memory_Gauge_00_needle.png")
OUT_DIR = os.path.join(ROOT, "memory_gauge")
SIZES = {"": 48, "@2x": 96}

# Mesures sur le cadran (887 px) : centre du trou central, où l'aiguille
# pivote, et arc des couleurs, 270° de 225° (0 %, bas gauche) à -45° (100 %,
# bas droite), degrés comptés dans le sens trigonométrique depuis l'axe
# horizontal droit. Vert, jaune et rouge y occupent chacun 90°, un tiers de
# l'échelle (seuils de memory_usage.memory_level).
DIAL_PIVOT = (443, 430)
ANGLE_AT_0 = 225.0
ANGLE_AT_100 = -45.0

# Mesures sur l'aiguille : centre de son rond (pivot) et pointe ; elle est
# dessinée pointe à gauche, très légèrement vers le bas.
NEEDLE_PIVOT = (381, 90.5)
NEEDLE_TIP = (0, 93.5)
# Distance pivot-pointe une fois posée sur le cadran : la pointe arrive au
# milieu de la couronne de couleur.
NEEDLE_LENGTH = 270


def needle_angle(percent):
    """Direction de l'aiguille, en degrés, pour un pourcentage de 0 à 100."""
    return ANGLE_AT_0 + (ANGLE_AT_100 - ANGLE_AT_0) * percent / 100


def gauge(percent, dial, needle):
    """Cadran et aiguille assemblés en pleine résolution (image RGBA)."""
    import math
    px, py = NEEDLE_PIVOT
    tx, ty = NEEDLE_TIP
    drawn_length = math.hypot(tx - px, ty - py)
    # direction dessinée, axe vertical retourné (l'image compte y vers le bas)
    drawn_angle = math.degrees(math.atan2(-(ty - py), tx - px))
    scale = NEEDLE_LENGTH / drawn_length
    small = needle.resize((round(needle.width * scale), round(needle.height * scale)),
                          Image.LANCZOS)
    layer = Image.new("RGBA", dial.size, (0, 0, 0, 0))
    cx, cy = DIAL_PIVOT
    layer.paste(small, (round(cx - px * scale), round(cy - py * scale)), small)
    # rotate : sens trigonométrique, autour du pivot
    layer = layer.rotate(needle_angle(percent) - drawn_angle, resample=Image.BICUBIC,
                         center=DIAL_PIVOT)
    return Image.alpha_composite(dial, layer)


def main(argv):
    dial = Image.open(DIAL).convert("RGBA")
    needle = Image.open(NEEDLE).convert("RGBA")
    if argv[:1] == ["--apercu"]:
        out = argv[1]
        values = (0, 33, 34, 50, 66, 67, 100)
        sheet = Image.new("RGBA", (len(values) * 300, 300 + 110), (255, 255, 255, 255))
        for i, value in enumerate(values):
            full = gauge(value, dial, needle)
            sheet.alpha_composite(full.resize((300, 300), Image.LANCZOS), (i * 300, 0))
            sheet.alpha_composite(full.resize((96, 96), Image.LANCZOS), (i * 300, 305))
            sheet.alpha_composite(full.resize((48, 48), Image.LANCZOS), (i * 300 + 110, 305))
        path = os.path.join(out, "apercu_jauges.png")
        sheet.save(path)
        print(path, "valeurs :", values)
        return
    os.makedirs(OUT_DIR, exist_ok=True)
    for value in range(101):
        full = gauge(value, dial, needle)
        for suffix, size in SIZES.items():
            full.resize((size, size), Image.LANCZOS).save(
                os.path.join(OUT_DIR, f"gauge_{value:03d}{suffix}.png"), optimize=True)
    print(f"{101 * len(SIZES)} images dans {OUT_DIR}")


if __name__ == "__main__":
    main(sys.argv[1:])
