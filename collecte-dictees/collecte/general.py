"""
Données de français général (Common Voice, Multilingual LibriSpeech...) ramenées à la convention
d'écriture, pour limiter l'« oubli » du modèle pendant le ré-entraînement.

Règles :
  - texte en minuscules, sans ponctuation : dans la parole générale, aucune commande n'est dictée ;
  - un énoncé est REJETÉ (jamais corrigé) s'il contient un chiffre, un nombre en lettres (la
    convention exige des chiffres, mais on ignore comment le lecteur a prononcé « 1995 »), un sigle,
    une abréviation, un symbole, ou s'il est refusé par le correcteur de convention ;
  - les voix du jeu de régression (general_test) ne sont jamais en entraînement ;
  - au plus N énoncés par voix, voix entrelacées : la diversité des voix compte plus que le volume.
"""
import hashlib
import re
import unicodedata
from collections import defaultdict

from . import texte as T

MOTS_MIN, MOTS_MAX = 2, 40
# « un » et « une » ne sont pas comptés : ce sont surtout des articles.
_NOMBRES = set(T._NOMBRES_LETTRES.split("|")) | {"zéro", "dizaine", "douzaine", "centaine", "millier", "million", "millions", "milliard", "milliards"}
_SYMBOLES = re.compile(r"[%€$£&@#/+=*<>§°|~^_\\\[\]{}]")
_ABREVIATIONS = re.compile(r"(?<!\w)(M|Mme|Mlle|MM|Dr|Pr|Me|St|Ste|etc|cf|ex|av|bd|n°)(?!\w)\.?", re.IGNORECASE)


def convertir(texte: str) -> tuple:
    """Phrase écrite d'un corpus général -> (texte selon la convention, None) ou (None, motif)."""
    t = unicodedata.normalize("NFC", texte or "").replace("’", "'").replace("‘", "'").strip()
    if not t:
        return None, "vide"
    if re.search(r"\d", t):
        return None, "chiffres"
    if _SYMBOLES.search(t):
        return None, "symbole"
    if _ABREVIATIONS.search(t):
        return None, "abréviation"
    for mot in re.findall(r"[^\W\d_]+", t):
        if len(mot) >= 2 and mot.isupper():
            return None, "sigle"
    t = re.sub(r"[^\w\s'-]", " ", t.lower())
    mots = []
    for m in t.split():
        m = m.strip("'-")
        if m:
            mots.append(m)
    for m in mots:
        if any(partie in _NOMBRES for partie in m.split("-")):
            return None, "nombre en lettres"
    if not MOTS_MIN <= len(mots) <= MOTS_MAX:
        return None, "longueur"
    sortie = " ".join(mots)
    if [m for niveau, m in T.verifier_texte(sortie, "phrase") if niveau == "erreur"]:
        return None, "convention"
    return sortie, None


def _cle(graine: str, valeur: str) -> str:
    return hashlib.sha1(f"{graine}|{valeur}".encode("utf-8")).hexdigest()


def repartir(enonces: list, n_test: int = 300, max_par_voix: int = 50, graine: str = "radia") -> tuple:
    """enonces : [{"voix", ...}]. Renvoie (réserve d'entraînement, jeu de régression).
    Les voix sont tirées dans un ordre déterministe ; le jeu de régression prend des voix ENTIÈRES
    jusqu'à n_test énoncés, toutes les autres voix alimentent l'entraînement."""
    par_voix = defaultdict(list)
    for e in enonces:
        par_voix[e["voix"]].append(e)
    voix = sorted(par_voix, key=lambda v: _cle(graine, v))
    for v in voix:                                    # ordre stable à l'intérieur d'une voix
        par_voix[v].sort(key=lambda e: _cle(graine, e.get("id", e.get("texte", ""))))
        del par_voix[v][max_par_voix:]
    test, reserve, k = [], [], 0
    while k < len(voix) and len(test) < n_test:
        test += par_voix[voix[k]][:max(1, min(max_par_voix, n_test // 10))]
        k += 1
    for v in voix[k:]:
        reserve += par_voix[v]
    return test[:n_test], entrelacer(reserve, graine)


def entrelacer(enonces: list, graine: str = "radia") -> list:
    """Ordre de sélection : un énoncé par voix à tour de rôle. Prendre un préfixe de cette liste
    donne autant de voix différentes que possible."""
    par_voix = defaultdict(list)
    for e in enonces:
        par_voix[e["voix"]].append(e)
    voix = sorted(par_voix, key=lambda v: _cle(graine, v))
    sortie, rang = [], 0
    while len(sortie) < len(enonces):
        for v in voix:
            if rang < len(par_voix[v]):
                sortie.append(par_voix[v][rang])
        rang += 1
    return sortie


def secondes_a_ajouter(secondes_radiologie: float, proportion: float) -> float:
    """Durée générale pour que le français général fasse « proportion » du mélange final.
    Exemple : 15 h de radiologie, proportion 0,25 -> 5 h de général (5 / 20 = 25 %)."""
    if not 0 <= proportion < 1:
        raise ValueError("la proportion doit être comprise entre 0 et 1 (exclu)")
    return secondes_radiologie * proportion / (1 - proportion)


def melanger(radiologie: list, general: list, proportion: float) -> list:
    """Manifeste mixte : toutes les lignes de radiologie + le début de la liste générale (déjà dans
    l'ordre de sélection) jusqu'à la durée voulue. Les mélanges successifs sont emboîtés."""
    besoin = secondes_a_ajouter(sum(float(l["duration"]) for l in radiologie), proportion)
    pris, total = [], 0.0
    for l in general:
        if total >= besoin:
            break
        pris.append(l)
        total += float(l["duration"])
    return radiologie + pris
