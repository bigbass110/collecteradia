"""
Construit des propositions de lexique à partir des erreurs réelles de Parakeet de base.

Les mots que Parakeet manque le plus souvent dans les données déjà collectées (lectures validées par
valider_enregistrements.py, segments de dictées réelles étiquetés) sont les vrais termes à cibler :
on ajoute ensuite des phrases qui les contiennent dans phrases.csv.

Usage (mêmes variables R2_* que les autres scripts ; sans elles, dossier local ./donnees) :
    python scripts/construire_lexique.py                      # écrit candidats_lexique.csv et affiche le bilan
    python scripts/construire_lexique.py --min-erreurs 3      # plus strict
    python scripts/construire_lexique.py --ajouter            # ajoute aussi les candidats à lexique.csv

--ajouter n'ajoute JAMAIS un mot avec une majuscule (nom propre possible : vérifiez qu'il ne s'agit
pas d'une personne avant de l'ajouter à la main). Relisez toujours la liste : un mot souvent manqué
peut aussi venir d'un texte de référence faux.

Confidentialité : candidats_lexique.csv ne contient que des mots isolés et des comptes, mais un nom
de personne mal filtré pourrait y figurer : ne le versionnez pas sans l'avoir relu.
"""
import argparse
import csv
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))
from collecte import erreurs as ER  # noqa: E402
from collecte import jeux as J  # noqa: E402
from collecte import texte as T  # noqa: E402
from collecte.stockage import depuis_environnement, lire_json  # noqa: E402

COLONNES = ["terme", "categorie", "erreurs", "occurrences", "taux", "voix", "deja_au_lexique",
            "majuscule", "confondu_avec", "ecrit_par_parakeet"]


def lire_journal(stockage) -> list:
    journal = []
    for cle in stockage.lister("journaux/"):
        if cle.endswith(".json"):
            journal += lire_json(stockage, cle, [])
    return journal


def construire(stockage, lexique: list, min_erreurs: int = 2, min_taux: float = 0.3) -> tuple:
    """Renvoie (candidats, bilan du lexique actuel, nombre de paires utilisées)."""
    p = ER.paires(lire_journal(stockage), lire_json(stockage, J.CLE_RESULTATS, {}),
                  lire_json(stockage, J.CLE_DECISIONS, {}))
    banque = RACINE / "phrases.csv"
    textes = [b["texte"] for b in T.lire_banque(banque.read_bytes())] if banque.exists() else []
    return (ER.candidats(ER.compter(p), lexique, min_erreurs, min_taux, textes),
            ER.bilan_lexique(p, lexique), len(p))


def ecrire_candidats(chemin: Path, candidats: list) -> None:
    with open(chemin, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLONNES)
        w.writeheader()
        w.writerows(candidats)


def ajouter_au_lexique(chemin: Path, candidats: list) -> list:
    """Ajoute à lexique.csv les candidats absents, sans majuscule. Renvoie les termes ajoutés."""
    nouveaux = [c for c in candidats if not c["deja_au_lexique"] and not c["majuscule"]]
    if nouveaux:
        texte = chemin.read_text(encoding="utf-8-sig")
        with open(chemin, "a", encoding="utf-8", newline="") as f:
            if texte and not texte.endswith("\n"):
                f.write("\n")
            w = csv.writer(f, lineterminator="\n")
            for c in nouveaux:
                w.writerow([c["terme"], c["categorie"]])
    return [c["terme"] for c in nouveaux]


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--lexique", type=Path, default=RACINE / "lexique.csv")
    p.add_argument("--sortie", type=Path, default=Path("candidats_lexique.csv"))
    p.add_argument("--min-erreurs", type=int, default=2, help="nombre minimal d'erreurs pour proposer un mot")
    p.add_argument("--min-taux", type=float, default=0.3, help="taux d'erreur minimal (0 à 1)")
    p.add_argument("--ajouter", action="store_true", help="ajoute les candidats sans majuscule à lexique.csv")
    a = p.parse_args(argv)

    lexique = T.lire_lexique(a.lexique.read_bytes())
    candidats, bilan, n = construire(depuis_environnement(), lexique, a.min_erreurs, a.min_taux)
    if not n:
        sys.exit("Aucune paire utilisable : lancez d'abord scripts/valider_enregistrements.py "
                 "ou l'étiquetage des dictées réelles.")
    ecrire_candidats(a.sortie, candidats)

    print(f"{n} enregistrement(s) au texte fiable analysé(s).\n")
    print(f"Mots les plus souvent manqués par Parakeet ({len(candidats)} candidat(s)) :")
    print(f"  {'terme':<26}{'erreurs':>8}{'taux':>8}{'voix':>6}  catégorie proposée                    Parakeet a écrit")
    for c in candidats[:25]:
        note = " (au lexique)" if c["deja_au_lexique"] else " (majuscule : nom ?)" if c["majuscule"] else ""
        cat = c["categorie"] + (f" ↔ {c['confondu_avec']}" if c["confondu_avec"] else "")
        print(f"  {c['terme'] + note:<26}{c['erreurs']:>8}{100 * c['taux']:>7.0f}%{c['voix']:>6}  "
              f"{cat:<37} {c['ecrit_par_parakeet']}")
    if bilan:
        print("\nLexique actuel : taux d'erreur de Parakeet de base sur chaque terme rencontré :")
        for b in bilan:
            print(f"  {b['terme']:<26}{100 * b['taux_erreur']:>6.0f}%  ({b['retrouves']}/{b['attendus']} retrouvés)")
        absents = sorted({l["terme"] for l in lexique if l["categorie"] not in {"commande", "negation", "lateralite"}}
                         - {b["terme"] for b in bilan})
        if absents:
            print(f"  Jamais rencontrés dans les données : {', '.join(absents)}")
    print(f"\nListe complète : {a.sortie}")
    if a.ajouter:
        ajoutes = ajouter_au_lexique(a.lexique, candidats)
        print(f"{len(ajoutes)} terme(s) ajouté(s) à {a.lexique.name} : {', '.join(ajoutes) or 'aucun'}.")
        if ajoutes:
            print("Ajoutez des phrases qui les contiennent dans phrases.csv "
                  "(python scripts/verifier_phrases.py liste ceux qui n'en ont pas).")


if __name__ == "__main__":
    main()
