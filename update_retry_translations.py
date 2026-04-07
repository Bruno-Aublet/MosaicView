#!/usr/bin/env python3
# update_retry_translations.py — Ajoute updates.retry dans toutes les locales

import json
import os

LOCALES_DIR = os.path.join(os.path.dirname(__file__), "locales")

# Traductions de "Réessayer" dans chaque langue
# fr.json déjà fait manuellement
TRANSLATIONS = {
    "ar":          "إعادة المحاولة",
    "bg":          "Опитай отново",
    "cs":          "Zkusit znovu",
    "da":          "Prøv igen",
    "de":          "Erneut versuchen",
    "el":          "Δοκιμή ξανά",
    "en":          "Retry",
    "es":          "Reintentar",
    "et":          "Proovi uuesti",
    "fi":          "Yritä uudelleen",
    "ga":          "Aththriail",
    "hi":          "पुनः प्रयास करें",
    "hr":          "Pokušaj ponovo",
    "hu":          "Újrapróbálás",
    "hy":          "Կրկին փորձել",
    "id":          "Coba lagi",
    "is":          "Reyna aftur",
    "it":          "Riprova",
    "ja":          "再試行",
    "ko":          "다시 시도",
    "lt":          "Bandyti dar kartą",
    "lv":          "Mēģināt vēlreiz",
    "ms":          "Cuba lagi",
    "mt":          "Erġa' pprova",
    "nl":          "Opnieuw proberen",
    "no":          "Prøv igjen",
    "pl":          "Spróbuj ponownie",
    "pt":          "Tentar novamente",
    "qya":         "Auta minna coivie",
    "ro":          "Reîncercați",
    "sjn":         "Garo velui",
    "sk":          "Skúsiť znova",
    "sl":          "Poskusi znova",
    "sv":          "Försök igen",
    "ta":          "மீண்டும் முயற்சி செய்",
    "th":          "ลองอีกครั้ง",
    "tlh":         "nIteb ghItlhqa'",
    "tr":          "Yeniden dene",
    "uk":          "Спробувати ще раз",
    "vi":          "Thử lại",
    "zh-CN":       "重试",
    "zh-TW":       "重試",
}

# Langues à ignorer (régénérées par des outils externes)
SKIP = {"fr", "qya-tengwar", "sjn-tengwar", "tlh-piqad", "language_names"}

updated = []
skipped = []
errors = []

for fname in sorted(os.listdir(LOCALES_DIR)):
    if not fname.endswith(".json"):
        continue
    lang = fname[:-5]
    if lang in SKIP:
        skipped.append(lang)
        continue
    if lang not in TRANSLATIONS:
        errors.append(f"MANQUANT: {lang}")
        continue

    fpath = os.path.join(LOCALES_DIR, fname)
    with open(fpath, encoding="utf-8") as f:
        data = json.load(f)

    if "updates" not in data:
        errors.append(f"Pas de section 'updates' dans {fname}")
        continue

    if "retry" in data["updates"]:
        skipped.append(f"{lang} (déjà présent)")
        continue

    data["updates"]["retry"] = TRANSLATIONS[lang]

    with open(fpath, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")

    updated.append(lang)

print(f"\nMis à jour ({len(updated)}) : {', '.join(updated)}")
print(f"Ignorés ({len(skipped)}) : {', '.join(skipped)}")
if errors:
    print(f"\nERREURS :")
    for e in errors:
        print(f"  {e}")
