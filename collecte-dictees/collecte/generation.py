"""
Génération de phrases à lire à partir des comptes rendus réels.

Les comptes rendus importés (historique/cr/) contiennent le vrai vocabulaire et les vraies tournures
des radiologues. On en tire des phrases courtes, réécrites selon la convention d'écriture
(« 12 x 8 mm, » -> « 12 fois 8 millimètres virgule »), pour enrichir phrases.csv.

Sécurité : une phrase est REJETÉE (jamais corrigée) dès qu'un doute existe :
  - ligne d'en-tête (nom, patient, né le, adressé par...), nom trouvé dans l'en-tête ;
  - date, civilité, âge (« 45 ans »), numéro long, adresse e-mail ;
  - mot avec majuscule en milieu de phrase qui n'est ni un sigle ni un éponyme du lexique ;
  - symbole sans forme parlée sûre (% / ° + = ...), abréviation (« cf. ») ;
  - phrase trop courte ou trop longue, ou refusée par le correcteur de convention.
Les phrases retenues sont des PROPOSITIONS : un radiologue les relit avant de les intégrer.
"""
import hashlib
import random
import re
import unicodedata
from collections import Counter

from . import etiquetage as E
from . import texte as T

MOTS_MIN = 4
# Catégories du lexique présentes dans presque toutes les phrases : elles ne guident pas la sélection
# (les négations et latéralités sont mesurées à l'évaluation, pas ciblées ici).
NON_CIBLES = {"commande", "unite", "negation", "lateralite"}
_UNITES = {"mm": "millimètres", "cm": "centimètres", "ml": "millilitres", "mL": "millilitres",
           "cc": "millilitres", "mm2": "millimètres carrés", "cm2": "centimètres carrés",
           "mm3": "millimètres cubes", "cm3": "centimètres cubes", "kg": "kilogrammes", "mg": "milligrammes",
           "UH": "unités Hounsfield", "HU": "unités Hounsfield"}
_SINGULIER = {"millimètres": "millimètre", "centimètres": "centimètre", "millilitres": "millilitre",
              "kilogrammes": "kilogramme", "milligrammes": "milligramme"}
_ROMAINS = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10,
            "XI": 11, "XII": 12}
_AVANT_ROMAIN = r"(?i:segments?|stade|grade|type|classe|niveau|catégorie|classification|bosniak)"
_FIN_PHRASE = re.compile(r"(?<=[.!?])\s+")
_INTERDITS = [
    (re.compile(r"\d{1,2}\s*[/.-]\s*\d{1,2}\s*[/.-]\s*\d{2,4}|\b(19|20)\d\d\b"), "date"),
    (re.compile(r"\b\d{1,2}\s+(janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|septembre|octobre|"
                r"novembre|décembre|decembre)\b", re.IGNORECASE), "date"),
    (re.compile(r"\b\d+\s*ans?\b", re.IGNORECASE), "âge"),
    (re.compile(r"\d{5,}"), "numéro"),
    (re.compile(r"\S+@\S+"), "adresse e-mail"),
    (re.compile(r"(?<!\w)(M|Mme|Mlle|Monsieur|Madame|Mademoiselle|Dr|Docteur|Pr|Professeur)(?!\w)"), "civilité"),
    (re.compile(r"[%/°+=<>&#@*\[\]{}«»\"…|~^]"), "symbole"),
]


def _unite(m: re.Match) -> str:
    nombre, unite = m.group(1), _UNITES[m.group(2)]
    if nombre in {"0", "1"} or re.fullmatch(r"[01],\d+", nombre):
        premier, *reste = unite.split()
        unite = " ".join([_SINGULIER.get(premier, premier)] + reste)
    return f"{nombre} {unite}"


def convertir(phrase: str, noms_propres: frozenset = frozenset(), noms_interdits: set = frozenset()) -> tuple:
    """Phrase écrite -> (texte selon la convention, None) ou (None, motif du rejet)."""
    s = unicodedata.normalize("NFC", phrase).replace("’", "'").strip()
    s = re.sub(r"^[-–•·*]\s*", "", s)                                   # puce de liste
    if E.MOTS_ENTETE.search(s):
        return None, "en-tête"
    s = re.sub(r"(\d)\.(\d)", r"\1,\2", s)                              # 3.5 -> 3,5
    s = re.sub(r"(\d)\s*[x×]\s*(?=\d)", r"\1 fois ", s)                 # 12 x 8 -> 12 fois 8
    for motif, raison in _INTERDITS:
        if motif.search(s):
            return None, raison
    unites = "|".join(sorted(map(re.escape, _UNITES), key=len, reverse=True))
    s = re.sub(rf"(\d+(?:,\d+)?)\s*({unites})(?![\w-])", _unite, s)
    s = re.sub(rf"\b({_AVANT_ROMAIN})\s+([IVX]+)\b",
               lambda m: f"{m.group(1)} {_ROMAINS[m.group(2)]}" if m.group(2) in _ROMAINS else m.group(0), s)

    fin = " point" if re.search(r"[.!?]\s*$", s) else ""
    s = re.sub(r"[.!?]+\s*$", "", s)
    if re.search(r"[.!?]", s):
        return None, "abréviation ou phrase coupée"
    s = re.sub(r"(?<!\d),|,(?!\d)", " virgule ", s)
    s = s.replace(";", " point-virgule ").replace(":", " deux points ")
    s = s.replace("(", " ouvrez la parenthèse ").replace(")", " fermez la parenthèse ")
    mots = s.split()
    if not mots:
        return None, "vide"

    sortie = []
    for k, m in enumerate(mots):
        nu = m.strip("'-")
        if nu.lower() in noms_interdits:
            return None, "nom de l'en-tête"
        if nu.isupper() or any(c.isdigit() for c in nu) or nu.lower() in noms_propres:
            sortie.append(m)
        elif nu[:1].isupper() and k > 0:
            return None, f"majuscule (nom propre ?) : {nu}"
        else:
            sortie.append(m.lower())
    texte = " ".join(sortie) + fin
    texte = re.sub(r"(?:\s*\b(virgule|point-virgule)\b)+\s+point$", " point", texte)   # « , . » -> point
    if len(texte.split()) < MOTS_MIN:
        return None, "trop courte"
    erreurs = [msg for niveau, msg in T.verifier_texte(texte, "phrase", noms_propres) if niveau == "erreur"]
    if erreurs:
        return None, f"convention : {erreurs[0]}"
    return texte, None


def phrases_du_compte_rendu(compte_rendu: str, noms_propres: frozenset = frozenset()) -> tuple:
    """Renvoie (phrases converties, Counter des motifs de rejet)."""
    _, noms = E.preparer_compte_rendu(compte_rendu)
    retenues, rejets = [], Counter()
    for ligne in compte_rendu.splitlines():
        if E.MOTS_ENTETE.search(ligne):
            rejets["en-tête"] += 1
            continue
        for morceau in _FIN_PHRASE.split(ligne.strip()):
            if not morceau.strip():
                continue
            texte, motif = convertir(morceau, noms_propres, noms)
            if texte:
                retenues.append(texte)
            else:
                rejets[motif.split(" :")[0]] += 1
    return retenues, rejets


def identifiant(texte: str) -> str:
    """Identifiant stable dérivé du texte : une phrase régénérée garde le même identifiant, donc le
    même ensemble (entraînement, validation ou test)."""
    return "g" + hashlib.sha1(T.normaliser(texte).encode("utf-8")).hexdigest()[:8]


def selectionner(candidats: dict, lexique: list, existants: list, maximum: int = 200,
                 max_par_terme: int = 5, poids: dict = None, graine: str = "radia") -> list:
    """candidats : {texte: {"modalite", "comptes_rendus", "radiologues"}}.
    Choisit d'abord les phrases qui couvrent les termes les plus utiles (poids : terme -> poids, par
    exemple le nombre d'erreurs de Parakeet), au plus max_par_terme phrases par terme, puis complète
    avec des phrases sans terme ciblé (diversité du vocabulaire). Écarte les textes déjà dans la banque."""
    poids = poids or {}
    categorie = {l["terme"]: l["categorie"] for l in lexique}
    deja = {T.normaliser(p["texte"]) for p in existants}
    ids_pris = {p["id"] for p in existants}
    rng = random.Random(graine)
    pool = []
    for texte, info in candidats.items():
        if T.normaliser(texte) in deja:
            continue
        termes = sorted(t for t in T.termes_de(texte, lexique) if categorie.get(t) not in NON_CIBLES)
        pool.append((texte, info, termes, rng.random()))
    couverture, choisies = Counter(), []

    def ajouter(texte, info, termes):
        ident = identifiant(texte)
        if ident in ids_pris:
            return
        ids_pris.add(ident)
        couverture.update(termes)
        choisies.append({"id": ident, "type": "phrase", "modalite": info.get("modalite") or "Autre",
                         "texte": texte, "termes": " ; ".join(termes),
                         "comptes_rendus": info.get("comptes_rendus", 1), "radiologues": info.get("radiologues", 1)})

    restants = [p for p in pool if p[2]]
    while restants and len(choisies) < maximum:
        def gain(p):
            return sum(poids.get(t, 1.0) for t in p[2] if couverture[t] < max_par_terme)
        meilleur = max(restants, key=lambda p: (gain(p), p[1].get("comptes_rendus", 1), p[3]))
        if gain(meilleur) <= 0:
            break
        restants.remove(meilleur)
        ajouter(*meilleur[:3])
    for texte, info, termes, _ in sorted((p for p in pool if not p[2]),
                                          key=lambda p: (-p[1].get("comptes_rendus", 1), p[3])):
        if len(choisies) >= maximum:
            break
        ajouter(texte, info, termes)
    return choisies
