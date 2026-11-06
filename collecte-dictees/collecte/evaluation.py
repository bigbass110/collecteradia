"""
Métriques d'évaluation d'un modèle de transcription de comptes rendus radiologiques.

Pour chaque énoncé, la référence (convention d'écriture) et l'hypothèse du modèle sont ramenées à la
même forme parlée et en minuscules (« 12 x 8 mm. » -> « 12 fois 8 millimètres »), puis on mesure :
  - WER : taux d'erreur par mot, commandes dictées comprises ;
  - WER hors commandes : sans « virgule », « point »... C'est la comparaison équitable avec Parakeet
    de base, qui écrit des signes au lieu des commandes ;
  - commandes : rappel (commandes dictées retrouvées) et précision ;
  - erreurs critiques : négation, latéralité, nombres différents (par énoncé concerné) ;
  - termes du lexique : rappel par catégorie (terme rare, score, paire dangereuse...).
Les intervalles de confiance sont obtenus par rééchantillonnage (bootstrap) des GROUPES — voix pour
les lectures, dictée pour les dictées réelles — car les énoncés d'une même voix ne sont pas indépendants.
"""
import random
import re
from collections import Counter, defaultdict

from . import etiquetage as E
from . import texte as T

NEGATIONS = {"pas", "sans", "aucun", "aucune", "absence", "ni", "non"}
LATERALITES = {"droit", "droite", "gauche", "bilatéral", "bilatérale", "bilatéraux"}
CATEGORIES_HORS_TERMES = {"negation", "lateralite", "commande"}   # mesurées à part
_NOMBRE = re.compile(r"\d+(?:,\d+)?")


def mots(texte: str) -> list:
    """Forme de comparaison : jetons en minuscules, abréviations remises sous forme parlée."""
    return [n for n, _ in E.jetons(texte or "", ecrit=True)]


def distance(a: list, b: list) -> int:
    """Distance d'édition entre deux listes de mots (substitutions + suppressions + insertions)."""
    precedent = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        courant = [i]
        for j, y in enumerate(b, 1):
            courant.append(min(precedent[j] + 1, courant[j - 1] + 1, precedent[j - 1] + (x != y)))
        precedent = courant
    return precedent[-1]


def _decouper_commandes(m: list) -> tuple:
    """Renvoie (commandes trouvées, mots restants). Les commandes les plus longues d'abord :
    « point à la ligne » n'est pas compté comme « point »."""
    commandes, reste, i = Counter(), [], 0
    while i < len(m):
        for c in E.COMMANDES_JETONS:
            if m[i:i + len(c)] == c:
                commandes[" ".join(c)] += 1
                i += len(c)
                break
        else:
            reste.append(m[i])
            i += 1
    return commandes, reste


def comparer(reference: str, hypothese: str, lexique: list) -> dict:
    """Résultat d'un énoncé, prêt à être agrégé par agreger()."""
    r, h = mots(reference), mots(hypothese)
    cmd_r, rs = _decouper_commandes(r)
    cmd_h, hs = _decouper_commandes(h)
    critiques = {}
    for nom, ensemble in (("negation", NEGATIONS), ("lateralite", LATERALITES)):
        a, b = Counter(x for x in rs if x in ensemble), Counter(x for x in hs if x in ensemble)
        if a or b:
            critiques[nom] = a != b
    na, nb = [x for x in rs if _NOMBRE.fullmatch(x)], [x for x in hs if _NOMBRE.fullmatch(x)]
    if na or nb:
        critiques["nombres"] = na != nb
    texte_r, texte_h = " ".join(r), " ".join(h)
    termes = {l["terme"]: T.contient(texte_h, l["norme"]) for l in lexique
              if l["categorie"] not in CATEGORIES_HORS_TERMES and T.contient(texte_r, l["norme"])}
    return {
        "erreurs": distance(r, h), "mots": len(r),
        "erreurs_hc": distance(rs, hs), "mots_hc": len(rs),
        "cmd_ref": sum(cmd_r.values()), "cmd_hyp": sum(cmd_h.values()), "cmd_ok": sum((cmd_r & cmd_h).values()),
        "critiques": critiques, "termes": termes,
    }


def _taux(num: int, den: int):
    return num / den if den else None


def agreger(resultats: list, lexique: list) -> dict:
    categorie = {l["terme"]: l["categorie"] for l in lexique}
    somme = lambda k: sum(r[k] for r in resultats)
    critiques = defaultdict(lambda: [0, 0])          # catégorie -> [énoncés en erreur, énoncés concernés]
    termes = defaultdict(lambda: [0, 0])             # catégorie -> [retrouvés, attendus]
    par_terme = defaultdict(lambda: [0, 0])
    for r in resultats:
        for nom, erreur in r["critiques"].items():
            critiques[nom][0] += erreur
            critiques[nom][1] += 1
        for terme, trouve in r["termes"].items():
            for cle, d in ((categorie.get(terme, "autre"), termes), (terme, par_terme)):
                d[cle][0] += trouve
                d[cle][1] += 1
    return {
        "enonces": len(resultats),
        "wer": _taux(somme("erreurs"), somme("mots")),
        "wer_hors_commandes": _taux(somme("erreurs_hc"), somme("mots_hc")),
        "commandes_rappel": _taux(somme("cmd_ok"), somme("cmd_ref")),
        "commandes_precision": _taux(somme("cmd_ok"), somme("cmd_hyp")),
        "critiques": {k: {"erreurs": e, "concernes": n, "taux": _taux(e, n)} for k, (e, n) in critiques.items()},
        "termes": {k: {"retrouves": t, "attendus": n, "rappel": _taux(t, n)} for k, (t, n) in termes.items()},
        "par_terme": {k: {"retrouves": t, "attendus": n} for k, (t, n) in par_terme.items()},
    }


def groupe_de(ligne: dict) -> str:
    """Unité de rééchantillonnage : la dictée pour une dictée réelle, la voix sinon."""
    if ligne.get("type") == "dictee_reelle":
        return "d:" + str(ligne.get("id_phrase", "")).split("_s")[0]
    return "v:" + str(ligne.get("code", ""))


def _sommes_par_groupe(resultats: list, groupes: list, cle: str, cle_n: str) -> dict:
    s = defaultdict(lambda: [0, 0])
    for r, g in zip(resultats, groupes):
        s[g][0] += r[cle]
        s[g][1] += r[cle_n]
    return s


def intervalle(resultats: list, groupes: list, cle: str = "erreurs_hc", cle_n: str = "mots_hc",
               ecart_avec: list = None, tirages: int = 1000, graine: int = 0):
    """Intervalle de confiance à 95 % d'un taux (WER par défaut), par bootstrap des groupes.
    Avec ecart_avec (résultats d'un AUTRE modèle sur les mêmes énoncés, dans le même ordre) : intervalle
    de l'écart (ce modèle - l'autre), apparié. Renvoie None s'il y a moins de 2 groupes."""
    a = _sommes_par_groupe(resultats, groupes, cle, cle_n)
    b = _sommes_par_groupe(ecart_avec, groupes, cle, cle_n) if ecart_avec is not None else None
    noms = sorted(a)
    if len(noms) < 2:
        return None
    rng, valeurs = random.Random(graine), []
    for _ in range(tirages):
        tirage = [rng.choice(noms) for _ in noms]
        n = sum(a[g][1] for g in tirage)
        if not n:
            continue
        v = sum(a[g][0] for g in tirage) / n
        if b is not None:
            v -= sum(b[g][0] for g in tirage) / n
        valeurs.append(v)
    valeurs.sort()
    if not valeurs:
        return None
    return valeurs[int(0.025 * (len(valeurs) - 1))], valeurs[int(0.975 * (len(valeurs) - 1))]
