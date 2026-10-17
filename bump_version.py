"""Change le numéro de version de l'application. Ouvrir ce fichier dans VSCode et cliquer sur ▶️ Run.

Fichiers modifiés : MosaicView.py, version_info.txt, index.html.
Le CHANGELOG.md n'est pas touché : son entrée se rédige à la main.
Aucun fichier n'est écrit si un seul des emplacements attendus est introuvable.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def _read(name):
    # newline="" garde les fins de ligne CRLF telles quelles
    with open(os.path.join(ROOT, name), encoding="utf-8", newline="") as f:
        return f.read()


def _write(name, text):
    with open(os.path.join(ROOT, name), "w", encoding="utf-8", newline="") as f:
        f.write(text)


def _current_version():
    m = re.search(r'^__version__ = "(\d+\.\d+\.\d+)"', _read("MosaicView.py"), re.M)
    if not m:
        sys.exit("Version actuelle introuvable dans MosaicView.py (__version__).")
    return m.group(1)


def _replace(text, pattern, repl, expected, label, errors, flags=0):
    """Remplace pattern ; expected = nombre exact attendu, ou (minimum,) pour un minimum."""
    new_text, n = re.subn(pattern, repl, text, flags=flags)
    ok = n >= expected[0] if isinstance(expected, tuple) else n == expected
    if not ok:
        wanted = f"au moins {expected[0]}" if isinstance(expected, tuple) else str(expected)
        errors.append(f"{label} : {n} occurrence(s) trouvée(s), {wanted} attendue(s)")
    return new_text, n


def main():
    old = _current_version()
    print(f"Version actuelle : {old}")
    new = input("Nouveau numéro de version (x.y.z) : ").strip().lstrip("vV")

    m = VERSION_RE.match(new)
    if not m:
        sys.exit(f"« {new} » n'est pas un numéro de la forme x.y.z. Rien n'a été modifié.")
    if new == old:
        sys.exit("C'est déjà la version actuelle. Rien n'a été modifié.")
    if tuple(map(int, m.groups())) < tuple(map(int, old.split("."))):
        if input(f"{new} est inférieur à {old}. Continuer quand même ? (o/N) : ").strip().lower() != "o":
            sys.exit("Abandon. Rien n'a été modifié.")

    x, y, z = m.groups()
    errors = []
    results = {}
    report = []

    # MosaicView.py : __version__
    text = _read("MosaicView.py")
    text, n = _replace(text, r'^__version__ = "\d+\.\d+\.\d+"', f'__version__ = "{new}"',
                       1, "MosaicView.py, __version__", errors, re.M)
    results["MosaicView.py"] = text
    report.append(f"MosaicView.py    : {n} remplacement(s)")

    # version_info.txt : filevers/prodvers (x, y, z, 0) et FileVersion/ProductVersion "x.y.z.0"
    text = _read("version_info.txt")
    text, n1 = _replace(text, r"\b(filevers|prodvers)=\(\d+, \d+, \d+, 0\)",
                        rf"\1=({x}, {y}, {z}, 0)",
                        2, "version_info.txt, filevers/prodvers", errors)
    text, n2 = _replace(text, r"(u'(?:FileVersion|ProductVersion)', u')\d+\.\d+\.\d+\.0'",
                        rf"\g<1>{new}.0'",
                        2, "version_info.txt, FileVersion/ProductVersion", errors)
    results["version_info.txt"] = text
    report.append(f"version_info.txt : {n1 + n2} remplacement(s)")

    # index.html : badge HTML (« &middot; »), ligne JS et valeurs figées des
    # langues fictives (« · ») ; le placeholder « v… » des autres langues n'est pas touché
    text = _read("index.html")
    text, n_html = _replace(text, r'(id="version-badge">)v\d+\.\d+\.\d+(?= &middot; Windows)',
                            rf"\g<1>v{new}", 1, "index.html, badge HTML", errors)
    text, n_js = _replace(text, r"(getElementById\('version-badge'\)\.textContent = ')v\d+\.\d+\.\d+(?= · Windows)",
                          rf"\g<1>v{new}", 1, "index.html, ligne JS du badge", errors)
    text, n_lang = _replace(text, r'("badge": ")v\d+\.\d+\.\d+(?= · Windows")',
                            rf"\g<1>v{new}", (0,), "index.html, badges des traductions", errors)
    results["index.html"] = text
    report.append(f"index.html       : {n_html + n_js + n_lang} remplacement(s)"
                  f" (dont {n_lang} dans les traductions)")

    if errors:
        print("\nERREUR — aucun fichier n'a été modifié :")
        for e in errors:
            print("  - " + e)
        sys.exit(1)

    for name, content in results.items():
        _write(name, content)

    print(f"\nVersion {old} -> {new}")
    for line in report:
        print("  " + line)

    # L'ancien numéro ne doit plus apparaître nulle part dans ces fichiers
    leftovers = [name for name, content in results.items() if old in content]
    if leftovers:
        print(f"\nATTENTION : « {old} » apparaît encore dans : {', '.join(leftovers)} — à vérifier.")
    print("\nReste à faire à la main : l'entrée [" + new + "] du CHANGELOG.md.")


if __name__ == "__main__":
    main()
