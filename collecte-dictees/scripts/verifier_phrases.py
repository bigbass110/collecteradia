"""
Vérifie phrases.csv et lexique.csv selon la convention d'écriture.
Échoue (code 1) s'il y a au moins une erreur ; les avertissements sont affichés sans bloquer.

Usage : python scripts/verifier_phrases.py
Lancé automatiquement par GitHub Actions à chaque envoi (.github/workflows/verifications.yml).
"""
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))
from collecte import texte as T  # noqa: E402

phrases = T.lire_banque((RACINE / "phrases.csv").read_bytes())
lexique = T.lire_lexique((RACINE / "lexique.csv").read_bytes())
problemes = T.verifier_banque(phrases, lexique)

erreurs = [p for p in problemes if p[0] == "erreur"]
for niveau, ou, message in problemes:
    print(f"{'ERREUR' if niveau == 'erreur' else 'avertissement'} [{ou}] {message}")
nb_phrases = sum(p["type"] == "phrase" for p in phrases)
nb_cr = sum(p["type"] == "compte_rendu" for p in phrases)
print(f"\n{nb_phrases} phrase(s), {nb_cr} compte(s) rendu(s), {len(lexique)} terme(s) du lexique : "
      f"{len(erreurs)} erreur(s), {len(problemes) - len(erreurs)} avertissement(s).")
sys.exit(1 if erreurs else 0)
