"""Couverture du lexique, statut de validation et instantanés versionnés des manifestes."""
import json
from collections import defaultdict
from datetime import datetime, timezone

from . import VERSION_APP
from .stockage import ecrire_json, lire_json
from .texte import ensemble_de, termes_de

CLE_RESULTATS = "validations/resultats.json"     # écrit par scripts/valider_enregistrements.py
CLE_DECISIONS = "validations/decisions.json"     # écrit par le coordinateur (onglet À vérifier)
CLE_VALIDATION_EN_COURS = "validations/en_cours.json"   # écrit par valider_enregistrements.py
DELAI_VERROU_S = 20 * 60                               # un marqueur plus ancien est considéré abandonné
ENSEMBLES = ["train", "val", "test_voix_connues", "test_voix_nouvelles", "test_comptes_rendus", "test_historique"]


def maj_resultats(stockage, nouveaux: dict = None, retires=()) -> dict:
    """Met à jour validations/resultats.json en relisant sa DERNIÈRE version juste avant d'écrire :
    chaque programme (étiquetage GPU, validation, retrait de consentement) ne modifie que ses propres
    lignes, et n'efface jamais celles qu'un autre a écrites entre-temps."""
    resultats = lire_json(stockage, CLE_RESULTATS, {})
    for f in retires:
        resultats.pop(f, None)
    resultats.update(nouveaux or {})
    ecrire_json(stockage, CLE_RESULTATS, resultats)
    return resultats


def _age_s(horodatage: str) -> float:
    return (datetime.now(timezone.utc) - datetime.fromisoformat(horodatage)).total_seconds()


def validation_en_cours(stockage) -> bool:
    """Vrai si valider_enregistrements.py tourne (marqueur mis à jour il y a moins de 20 minutes)."""
    maj = lire_json(stockage, CLE_VALIDATION_EN_COURS, {}).get("maj")
    return bool(maj) and _age_s(maj) < DELAI_VERROU_S


def marquer_validation(stockage, active: bool) -> None:
    if active:
        ecrire_json(stockage, CLE_VALIDATION_EN_COURS,
                    {"maj": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    else:
        stockage.supprimer(CLE_VALIDATION_EN_COURS)


def couverture(journal: list, lexique: list) -> dict:
    """terme -> nombre de voix distinctes l'ayant prononcé."""
    voix = defaultdict(set)
    for j in journal:
        for t in termes_de(j.get("texte_final", ""), lexique):
            voix[t].add(j["code"])
    return {t: len(v) for t, v in voix.items()}


def statut(fichier: str, resultats: dict, decisions: dict) -> str:
    """'valide_humain', 'rejete', 'a_verifier', 'ok' (validé automatiquement) ou 'non_valide'."""
    if fichier in decisions:
        return decisions[fichier]["statut"]
    if fichier in resultats:
        return resultats[fichier]["statut"]
    return "non_valide"


def destination(entree: dict, test_locuteurs: set) -> str:
    """Les segments d'une même dictée réelle (même « groupe ») vont toujours dans le même ensemble."""
    if entree.get("type") == "compte_rendu":
        return "test_comptes_rendus"
    if entree.get("type") == "dictee_reelle":
        groupe = entree.get("groupe", entree["id_phrase"])
        if entree["code"] in test_locuteurs or ensemble_de(groupe) == "test":
            return "test_historique"
        return ensemble_de(groupe)
    if entree["code"] in test_locuteurs:
        return "test_voix_nouvelles"
    e = ensemble_de(entree["id_phrase"])
    return "test_voix_connues" if e == "test" else e


def creer_instantane(stockage, journal: list, test_locuteurs: set, version_banque: str,
                     exiger_validation: bool) -> dict:
    """Écrit manifestes/<horodatage>/<ensemble>.json + resume.json, et met à jour
    manifestes/dernier.json. Les instantanés ne sont jamais modifiés ensuite."""
    resultats = lire_json(stockage, CLE_RESULTATS, {})
    decisions = lire_json(stockage, CLE_DECISIONS, {})
    nom = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    contenu = {e: [] for e in ENSEMBLES}
    exclus = defaultdict(int)

    for j in journal:
        s = statut(j["fichier"], resultats, decisions)
        if s in ("rejete", "a_verifier") or (exiger_validation and s == "non_valide"):
            exclus[s] += 1
            continue
        dest = destination(j, test_locuteurs)
        if dest == "test_historique" and s != "valide_humain":
            # un jeu de test doit être une vérité vérifiée : seuls les segments relus entrent
            exclus["test_historique_a_relire"] += 1
            continue
        contenu[dest].append({
            "audio_filepath": j["fichier"], "duration": float(j["duree_s"]),
            "text": j["texte_final"], "code": j["code"], "id_phrase": j["id_phrase"],
            "type": j.get("type", "phrase"), "source": j.get("source", "lecture"),
            "micro": j.get("micro", ""), "lieu": j.get("lieu", ""),
        })

    resume = {
        "instantane": nom, "cree_le": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "version_app": VERSION_APP, "version_banque": version_banque,
        "exiger_validation": exiger_validation, "test_locuteurs": sorted(test_locuteurs),
        "exclus": dict(exclus), "ensembles": {},
    }
    for e, lignes in contenu.items():
        stockage.ecrire(f"manifestes/{nom}/{e}.json",
                        "".join(json.dumps(l, ensure_ascii=False) + "\n" for l in lignes).encode("utf-8"))
        resume["ensembles"][e] = {
            "enregistrements": len(lignes),
            "heures": round(sum(l["duration"] for l in lignes) / 3600, 3),
            "voix": len({l["code"] for l in lignes}),
            "phrases": len({l["id_phrase"] for l in lignes}),
        }
    ecrire_json(stockage, f"manifestes/{nom}/resume.json", resume)
    ecrire_json(stockage, "manifestes/dernier.json", {"instantane": nom})
    return resume


def lister_instantanes(stockage) -> list:
    noms = {c.split("/")[1] for c in stockage.lister("manifestes/") if c.count("/") >= 2}
    return sorted(noms, reverse=True)


# ─────────────────────────── Ordre de l'onglet « À vérifier » ───────────────────────────

# (points, motif affiché, mots repérés dans les raisons). Les cas qui apprennent le plus au modèle,
# ou sans lesquels un jeu de test reste incomplet, passent en premier ; un compte rendu mal apparié,
# qui fera surtout perdre du temps, passe en dernier.
VALEURS = [
    (4, "erreur critique du modèle de base", ("négation", "latéralité", "nombres")),
    (3, "correction du compte rendu à confirmer", ("correction « ", "correction de groupe")),
    (2, "réservé au test", ("réservé au test",)),
    (1, "nom propre possible", ("nom propre possible",)),
    (1, "commande dictée possible", ("ponctuation « ",)),
    (1, "désaccord entre sources", ("désaccord avec le compte rendu", "mot entendu par un seul moteur")),
    (-3, "compte rendu peut-être mal apparié", ("mal apparié",)),
]
POINTS_TERME, TERMES_MAX = 2, 3
_NON_CIBLES = {"commande", "unite", "negation", "lateralite"}


def valeur_verification(entree: dict, resultat: dict, lexique: list) -> tuple:
    """Renvoie (points, [motifs]) d'un enregistrement à vérifier. Chaque terme du lexique présent dans
    le texte mais absent de la transcription de Parakeet vaut des points : c'est précisément ce que le
    ré-entraînement doit apprendre."""
    raisons = " ; ".join(resultat.get("raisons", []))
    points, motifs = 0, []
    for p, motif, mots in VALEURS:
        if any(m in raisons for m in mots):
            points += p
            motifs.append(motif)
    hypothese = entree.get("texte_parakeet") or resultat.get("hypothese", "")
    categorie = {l["terme"]: l["categorie"] for l in lexique}
    manques = sorted(t for t in termes_de(entree.get("texte_final", ""), lexique) - termes_de(hypothese, lexique)
                     if categorie.get(t) not in _NON_CIBLES)
    if manques:
        points += POINTS_TERME * min(len(manques), TERMES_MAX)
        motifs.insert(0, "terme manqué : " + ", ".join(manques[:TERMES_MAX]))
    return points, motifs


def trier_a_verifier(fichiers: list, par_fichier: dict, resultats: dict, lexique: list) -> list:
    """[(fichier, points, motifs)] du plus utile au moins utile ; à valeur égale, les plus courts
    d'abord (plus vite vérifiés), puis par nom pour un ordre stable."""
    lignes = [(f, *valeur_verification(par_fichier[f], resultats[f], lexique)) for f in fichiers]
    return sorted(lignes, key=lambda x: (-x[1], float(par_fichier[x[0]].get("duree_s", 0)), x[0]))
