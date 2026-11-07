"""
Génère des phrases à lire à partir des comptes rendus réels, puis les intègre à phrases.csv après relecture.

Étape 1 : proposer (mêmes variables R2_* que les autres scripts ; sans elles, dossier local ./donnees)
    python scripts/generer_phrases.py                                  # comptes rendus importés
    python scripts/generer_phrases.py --dossier ~/comptes_rendus --modalite Thorax   # .txt, .docx, .pdf
    python scripts/generer_phrases.py --candidats candidats_lexique.csv --max 300
  -> phrases_proposees.csv : phrases réécrites selon la convention, triées par utilité
     (termes du lexique couverts, pondérés par les erreurs de Parakeet si --candidats est donné).

Étape 2 : RELIRE phrases_proposees.csv (un radiologue) : supprimer les lignes fausses, cliniquement
    douteuses ou trop proches d'un patient précis ; corriger une modalité si besoin.

Étape 3 : intégrer
    python scripts/generer_phrases.py --integrer phrases_proposees.csv
  -> ajoute les lignes restantes à phrases.csv (revérifiées), puis git commit / git push.

Confidentialité : phrases_proposees.csv contient des extraits de comptes rendus. Il est ignoré par
Git ; supprimez-le après intégration.
"""
import argparse
import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))
from collecte import generation as G  # noqa: E402
from collecte import importation as I  # noqa: E402
from collecte import texte as T  # noqa: E402
from collecte.stockage import depuis_environnement, lire_json  # noqa: E402

COLONNES = ["id", "type", "modalite", "texte", "termes", "comptes_rendus", "radiologues"]


# ─────────────────────────── Sources ───────────────────────────

def comptes_rendus_importes(stockage) -> list:
    """[(code du radiologue, modalité, texte)] depuis historique/index/ et historique/cr/."""
    sortie = []
    for cle in stockage.lister("historique/index/"):
        for e in lire_json(stockage, cle, []):
            brut = stockage.lire(f"historique/cr/{e['id']}.txt")
            if brut:
                sortie.append((e.get("code", ""), e.get("modalite", ""), brut.decode("utf-8")))
    return sortie


def comptes_rendus_du_dossier(dossier: Path, modalite: str) -> list:
    sortie = []
    for f in sorted(dossier.rglob("*")):
        if f.is_file() and f.suffix.lower() in I.EXT_TEXTE:
            try:
                sortie.append(("dossier", modalite, I.extraire_texte(f.name, f.read_bytes())))
            except Exception as erreur:      # un fichier illisible n'arrête pas le reste
                print(f"  ignoré ({type(erreur).__name__}) : {f.name}")
    return sortie


# ─────────────────────────── Étape 1 : proposer ───────────────────────────

def proposer(comptes_rendus: list, lexique: list, existants: list, maximum: int = 200,
             max_par_terme: int = 5, poids: dict = None) -> tuple:
    """Renvoie (phrases choisies, Counter des rejets, nombre de phrases candidates distinctes)."""
    noms = T.noms_propres_du_lexique(lexique)
    candidats = {}
    vus_par = defaultdict(set)
    rejets = Counter()
    for code, modalite, texte in comptes_rendus:
        phrases, r = G.phrases_du_compte_rendu(texte, noms)
        rejets.update(r)
        for p in set(phrases):
            info = candidats.setdefault(p, {"modalite": modalite, "comptes_rendus": 0})
            info["comptes_rendus"] += 1
            info["modalite"] = info["modalite"] or modalite
            vus_par[p].add(code)
    for p, info in candidats.items():
        info["radiologues"] = len(vus_par[p])
    return G.selectionner(candidats, lexique, existants, maximum, max_par_terme, poids), rejets, len(candidats)


def lire_poids(chemin: Path) -> dict:
    """Poids des termes depuis candidats_lexique.csv (construire_lexique.py) : 1 + nombre d'erreurs."""
    with open(chemin, encoding="utf-8") as f:
        return {l["terme"]: 1.0 + float(l.get("erreurs") or 0) for l in csv.DictReader(f)}


def ecrire_propositions(chemin: Path, phrases: list) -> None:
    with open(chemin, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLONNES)
        w.writeheader()
        w.writerows(phrases)


# ─────────────────────────── Étape 3 : intégrer ───────────────────────────

def integrer(propositions: Path, banque: Path, lexique: list) -> tuple:
    """Ajoute à phrases.csv les propositions relues. Chaque ligne est revérifiée : convention,
    identifiant libre, texte absent de la banque. Renvoie (ajoutées, [(id, motif du refus)])."""
    existants = T.lire_banque(banque.read_bytes())
    ids = {p["id"] for p in existants}
    textes = {T.normaliser(p["texte"]) for p in existants}
    noms = T.noms_propres_du_lexique(lexique)
    ajoutees, refus = [], []
    # Lecture directe du CSV : une virgule non protégée par des guillemets, ajoutée à la relecture,
    # coupe le texte en deux colonnes ; lire_banque garderait silencieusement la première moitié.
    with open(propositions, encoding="utf-8-sig", newline="") as f:
        lignes = list(csv.DictReader(f))
    for ligne in lignes:
        p = {"id": (ligne.get("id") or "").strip(), "type": (ligne.get("type") or "phrase").strip(),
             "modalite": (ligne.get("modalite") or "Autre").strip(), "texte": " ".join((ligne.get("texte") or "").split())}
        erreurs = [m for n, m in T.verifier_texte(p["texte"], p["type"], noms) if n == "erreur"]
        if ligne.get(None):
            refus.append((p["id"] or "(sans id)", "colonne en trop : virgule non protégée dans le texte ? "
                                                  "écrire « virgule » ou mettre le texte entre guillemets"))
        elif not p["id"] or p["type"] != "phrase":
            refus.append((p["id"] or "(sans id)", "identifiant ou type invalide"))
        elif erreurs:
            refus.append((p["id"], erreurs[0]))
        elif p["id"] in ids:
            refus.append((p["id"], "identifiant déjà utilisé"))
        elif T.normaliser(p["texte"]) in textes:
            refus.append((p["id"], "texte déjà dans la banque"))
        else:
            ids.add(p["id"])
            textes.add(T.normaliser(p["texte"]))
            ajoutees.append(p)
    if ajoutees:
        contenu = banque.read_text(encoding="utf-8-sig")
        with open(banque, "a", encoding="utf-8", newline="") as f:
            if contenu and not contenu.endswith("\n"):
                f.write("\n")
            w = csv.writer(f, lineterminator="\n")
            for p in ajoutees:
                w.writerow([p["id"], "phrase", p["modalite"], p["texte"]])
    return ajoutees, refus


# ─────────────────────────── Programme ───────────────────────────

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--dossier", type=Path, help="dossier de comptes rendus (.txt, .docx, .pdf) au lieu des imports")
    p.add_argument("--modalite", default="", help="modalité des comptes rendus de --dossier")
    p.add_argument("--candidats", type=Path, help="candidats_lexique.csv : pondère les termes par les erreurs")
    p.add_argument("--max", type=int, default=200, help="nombre maximal de phrases proposées")
    p.add_argument("--max-par-terme", type=int, default=5, help="phrases au plus par terme ciblé")
    p.add_argument("--sortie", type=Path, default=Path("phrases_proposees.csv"))
    p.add_argument("--integrer", type=Path, help="propositions relues à ajouter à phrases.csv")
    p.add_argument("--banque", type=Path, default=RACINE / "phrases.csv")
    p.add_argument("--lexique", type=Path, default=RACINE / "lexique.csv")
    a = p.parse_args(argv)
    lexique = T.lire_lexique(a.lexique.read_bytes())

    if a.integrer:
        ajoutees, refus = integrer(a.integrer, a.banque, lexique)
        for ident, motif in refus:
            print(f"  refusée [{ident}] : {motif}")
        print(f"{len(ajoutees)} phrase(s) ajoutée(s) à {a.banque.name}, {len(refus)} refusée(s).")
        if ajoutees:
            print("Vérifiez avec python scripts/verifier_phrases.py, puis git commit et git push. "
                  f"Supprimez ensuite {a.integrer.name} (extraits de comptes rendus).")
        return

    comptes_rendus = (comptes_rendus_du_dossier(a.dossier.expanduser(), a.modalite) if a.dossier
                      else comptes_rendus_importes(depuis_environnement()))
    if not comptes_rendus:
        sys.exit("Aucun compte rendu : importez des dictées dans l'application, ou utilisez --dossier.")
    poids = lire_poids(a.candidats) if a.candidats else None
    choisies, rejets, n = proposer(comptes_rendus, lexique, T.lire_banque(a.banque.read_bytes()),
                                   a.max, a.max_par_terme, poids)
    ecrire_propositions(a.sortie, choisies)

    print(f"{len(comptes_rendus)} compte(s) rendu(s) : {n} phrase(s) distincte(s) convertible(s), "
          f"{sum(rejets.values())} rejetée(s).")
    if rejets:
        print("  Rejets : " + ", ".join(f"{m} {k}" for m, k in rejets.most_common()))
    avec = sum(1 for c in choisies if c["termes"])
    print(f"{len(choisies)} phrase(s) proposée(s), dont {avec} avec un terme du lexique.")
    for c in choisies[:10]:
        print(f"  [{c['id']}] {c['texte']}" + (f"   ({c['termes']})" if c["termes"] else ""))
    print(f"\nÀ relire par un radiologue : {a.sortie}\n"
          f"Puis : python scripts/generer_phrases.py --integrer {a.sortie}")


if __name__ == "__main__":
    main()
