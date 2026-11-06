"""
Mots que Parakeet de base transcrit mal, tirés des données déjà collectées : la base d'un lexique
construit à partir des erreurs réelles plutôt qu'à la main.

Sources (paires « texte de référence » / « sortie de Parakeet de base ») :
  - lectures : texte lu (journal) / hypothèse de valider_enregistrements.py (validations/resultats.json) ;
  - dictées réelles : texte du segment (corrigé par le compte rendu, Whisper ou le coordinateur) /
    texte_parakeet du segment (journal).
Seuls les enregistrements au texte fiable sont utilisés : validés automatiquement (« ok ») ou relus
(« valide_humain »). Les cas « à vérifier » non tranchés et les rejets sont ignorés.

Pour chaque mot de la référence, on aligne avec l'hypothèse et on compte combien de fois Parakeet
l'a manqué, et ce qu'il a écrit à la place. Les mots outils, nombres, commandes et hésitations sont
ignorés : ce ne sont pas des termes de lexique.
"""
import difflib
import re
from collections import Counter, defaultdict

from . import etiquetage as E
from . import jeux as J
from . import texte as T

STATUTS_FIABLES = {"ok", "valide_humain"}
MOTS_OUTILS = {
    "a", "à", "au", "aux", "avec", "ce", "ces", "cet", "cette", "d", "dans", "de", "des", "du", "en", "est",
    "et", "il", "elle", "ils", "elles", "l", "la", "le", "les", "leur", "mais", "ne", "ou", "où", "par",
    "pour", "qu", "que", "qui", "sa", "se", "son", "ses", "sur", "un", "une", "y", "on", "plus", "très",
}
SIMILARITE_PAIRE = 0.6


_ELISION = re.compile(r"^(?:d|l|qu|n|s|j|c|m|t|jusqu|lorsqu|puisqu)'(?=\w)", re.IGNORECASE)


def _mots(texte: str) -> list:
    """(norme, affichage) des mots, sans les commandes dictées ni l'élision (« d'adénopathie » -> « adénopathie »)."""
    jets = [(_ELISION.sub("", n), _ELISION.sub("", a)) for n, a in E.jetons(texte or "", ecrit=True)]
    normes, sortie, i = [n for n, _ in jets], [], 0
    while i < len(jets):
        for c in E.COMMANDES_JETONS:
            if normes[i:i + len(c)] == c:
                i += len(c)
                break
        else:
            sortie.append(jets[i])
            i += 1
    return sortie


def _ignore(norme: str) -> bool:
    nu = norme.strip("'")
    return (nu in MOTS_OUTILS or nu in E.HESITATIONS or len(nu) < 3
            or any(c.isdigit() for c in nu) or norme.endswith("'"))


def paires(journal: list, resultats: dict, decisions: dict) -> list:
    """[(code, référence, hypothèse de Parakeet)] pour les enregistrements au texte fiable."""
    sortie = []
    for j in journal:
        if J.statut(j["fichier"], resultats, decisions) not in STATUTS_FIABLES:
            continue
        if j.get("type") == "dictee_reelle":
            hyp = j.get("texte_parakeet")
        else:
            r = resultats.get(j["fichier"], {})
            hyp = r.get("hypothese") if "parakeet" in str(r.get("modele", "")).lower() else None
        if hyp:
            sortie.append((j["code"], j["texte_final"], hyp))
    return sortie


def compter(paires_: list) -> dict:
    """mot -> statistiques d'erreur, à partir de paires (code, référence, hypothèse)."""
    stats = defaultdict(lambda: {"occurrences": 0, "erreurs": 0, "voix": set(), "voix_erreur": set(),
                                 "ecrit": Counter(), "formes": Counter()})
    for code, ref, hyp in paires_:
        r, h = _mots(ref), _mots(hyp)
        rn, hn = [n for n, _ in r], [n for n, _ in h]
        for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, rn, hn, autojunk=False).get_opcodes():
            ecrit = " ".join(a for _, a in h[j1:j2]) or "∅"
            for i in range(i1, i2):
                norme, forme = r[i]
                if _ignore(norme):
                    continue
                s = stats[norme]
                s["occurrences"] += 1
                s["voix"].add(code)
                s["formes"][forme] += 1
                if op != "equal":
                    s["erreurs"] += 1
                    s["voix_erreur"].add(code)
                    s["ecrit"][ecrit] += 1
    return stats


def candidats(stats: dict, lexique: list, min_erreurs: int = 2, min_taux: float = 0.3,
              textes_connus: list = ()) -> list:
    """Mots les plus souvent manqués, avec une catégorie proposée :
    « paire_dangereuse » si Parakeet écrit à la place un AUTRE mot réel et proche (hypo/hyper,
    intra/extra), sinon « terme_rare ». Un mot est « réel » s'il figure dans une référence, dans le
    lexique ou dans textes_connus (la banque de phrases). Un terme déjà au lexique garde sa catégorie.
    Triés par nombre d'erreurs."""
    deja = {l["norme"]: l["categorie"] for l in lexique}
    vocabulaire = set(stats) | {n for t in [l["terme"] for l in lexique] + list(textes_connus) for n, _ in _mots(t)}
    sortie = []
    for norme, s in stats.items():
        taux = s["erreurs"] / s["occurrences"]
        if s["erreurs"] < min_erreurs or taux < min_taux:
            continue
        forme = s["formes"].most_common(1)[0][0]
        proche = ""
        for ecrit, _ in s["ecrit"].most_common(3):
            e = ecrit.lower()
            if e != norme and e in vocabulaire and E.similaires(e, norme, SIMILARITE_PAIRE):
                proche = e
                break
        sortie.append({
            "terme": forme,                     # forme écrite de la référence (convention)
            "categorie": deja.get(norme) or ("paire_dangereuse" if proche else "terme_rare"),
            "erreurs": s["erreurs"], "occurrences": s["occurrences"], "taux": round(taux, 3),
            "voix": len(s["voix_erreur"]), "deja_au_lexique": norme in deja,
            "majuscule": forme[:1].isupper() and not forme.isupper(),
            "confondu_avec": proche,
            "ecrit_par_parakeet": "; ".join(f"{e} ×{n}" for e, n in s["ecrit"].most_common(3)),
        })
    return sorted(sortie, key=lambda c: (-c["erreurs"], -c["taux"], c["terme"]))


def bilan_lexique(paires_: list, lexique: list) -> list:
    """Pour chaque terme du lexique actuel (expressions comprises) : combien de fois Parakeet de base
    l'a retrouvé. Un terme toujours retrouvé n'a peut-être plus besoin d'être ciblé."""
    trouve = defaultdict(lambda: [0, 0])
    for _, ref, hyp in paires_:
        r = " ".join(n for n, _ in _mots(ref))
        h = " ".join(n for n, _ in _mots(hyp))
        for l in lexique:
            if l["categorie"] in {"commande", "negation", "lateralite"}:
                continue
            if T.contient(r, l["norme"]):
                trouve[l["terme"]][1] += 1
                trouve[l["terme"]][0] += T.contient(h, l["norme"])
    return sorted(({"terme": t, "categorie": next(l["categorie"] for l in lexique if l["terme"] == t),
                    "attendus": n, "retrouves": ok, "taux_erreur": round(1 - ok / n, 3)}
                   for t, (ok, n) in trouve.items()), key=lambda b: -b["taux_erreur"])
