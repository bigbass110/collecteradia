"""
Étiquetage faiblement supervisé des dictées historiques.

Entrées pour une dictée :
  - mots_p : transcription Parakeet avec horodatage par mot  [{"mot", "debut", "fin"}]
  - mots_w : transcription Whisper avec horodatage par mot   [{"mot", "debut", "fin"}]
  - compte_rendu : texte du compte rendu final

Principe : la transcription Parakeet donne la chronologie (qui a dit quoi, quand). Le compte rendu
sert de GUIDE, jamais de cible :
  - mot identique au compte rendu                  -> fiable
  - mot proche d'un mot du compte rendu            -> corrigé par le compte rendu (« spécule » -> « spiculé »),
                                                      fiable seulement si Whisper a aussi entendu le mot du compte rendu
  - mot absent du compte rendu                     -> commande dictée (fiable), hésitation (supprimée),
                                                      ou mot entendu par les deux moteurs (fiable), sinon à vérifier
  - texte du compte rendu jamais prononcé          -> IGNORÉ (texte de modèle, en-tête, reformulation)
Les segments contenant un nom, une date ou un âge prononcés sont exclus.
"""
import difflib
import re
import unicodedata

from . import texte as T

HESITATIONS = {"euh", "heu", "hum", "hmm", "euhm", "bah", "ben", "hein"}
CIVILITES = {"monsieur", "madame", "mademoiselle", "mme", "mlle", "m", "dr", "docteur", "pr", "professeur"}
MOIS = {"janvier", "février", "fevrier", "mars", "avril", "mai", "juin", "juillet", "août", "aout",
        "septembre", "octobre", "novembre", "décembre", "decembre"}
MOTS_ENTETE = re.compile(r"\b(nom|prénom|prenom|patient|patiente|né|née|naissance|adressé|adressée|"
                         r"demandeur|prescripteur|docteur|dr|ipp|dossier|n°)\b", re.IGNORECASE)
FINS_DE_PHRASE = [["point"], ["point", "à", "la", "ligne"], ["à", "la", "ligne"], ["nouveau", "paragraphe"]]
COMMANDES_JETONS = sorted((c.split() for c in T.COMMANDES), key=len, reverse=True)

SEG_MAX, SEG_MIN, SEG_CIBLE = 20.0, 1.5, 5.0
PAUSE_COUPE = 0.6
MARGE = 0.15
SIMILARITE_MOT, SIMILARITE_BLOC = 0.6, 0.8
CONCORDANCE_MIN = 0.3

_UNITES = {"mm": "millimètres", "cm": "centimètres", "ml": "millilitres", "mL": "millilitres",
           "UH": "unités Hounsfield", "HU": "unités Hounsfield"}


# ─────────────────────────── Jetons ───────────────────────────

def jetons(texte: str, ecrit: bool = False) -> list:
    """Découpe en jetons (forme normalisée, forme affichée). ecrit=True : texte de compte rendu, on
    remet les abréviations sous leur forme parlée (12 x 8 mm -> 12 fois 8 millimètres)."""
    t = unicodedata.normalize("NFC", texte).replace("’", "'")
    if ecrit:
        t = re.sub(r"(\d)(mm|cm|ml|mL)\b", r"\1 \2", t)
        t = re.sub(r"(\d)\s*[x×]\s*(?=\d)", r"\1 fois ", t)
        t = re.sub(r"(\d)\.(\d)", r"\1,\2", t)
        t = re.sub(r"(?<![\w-])(mm|cm|ml|mL|UH|HU)(?![\w-])", lambda m: _UNITES[m.group(1)], t)
    t = re.sub(r"(?<=\d),(?=\d)", "§", t)
    t = re.sub(r"[^\w\s'§-]", " ", t)
    sortie = []
    for m in t.split():
        m = m.replace("§", ",").strip("-")
        if m and m != "'":
            sortie.append((m.lower(), m))
    return sortie


def forme(affichage: str, noms_propres: frozenset) -> str:
    """Convention : minuscules sauf sigles, mots contenant un chiffre et noms propres du lexique."""
    if affichage.isupper() or any(c.isdigit() for c in affichage) or affichage.lower() in noms_propres:
        return affichage
    return affichage.lower()


def _sans_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def similaires(a: str, b: str, seuil: float) -> bool:
    return difflib.SequenceMatcher(None, _sans_accents(a), _sans_accents(b)).ratio() >= seuil


# ─────────────────────────── Compte rendu ───────────────────────────

def preparer_compte_rendu(compte_rendu: str) -> tuple:
    """Retire les lignes d'en-tête identifiantes et collecte les noms qu'elles contiennent.
    Renvoie (jetons du compte rendu, noms à repérer dans l'audio)."""
    noms, gardees = set(), []
    for ligne in compte_rendu.splitlines():
        if MOTS_ENTETE.search(ligne):
            for norm, aff in jetons(ligne):
                if aff[:1].isupper() and len(norm) > 2 and not MOTS_ENTETE.fullmatch(norm):
                    noms.add(norm)
        else:
            gardees.append(ligne)
    return jetons("\n".join(gardees), ecrit=True), noms


# ─────────────────────────── Étiquetage ───────────────────────────

def _aligner(a: list, b: list) -> list:
    return difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes()


def _egalites(a: list, b: list) -> dict:
    """Index de a -> index de b pour les jetons identiques alignés."""
    paires = {}
    for op, i1, i2, j1, j2 in _aligner(a, b):
        if op == "equal":
            for k in range(i2 - i1):
                paires[i1 + k] = j1 + k
    return paires


def _jetons_mots(mots: list) -> list:
    """Jetons d'une transcription horodatée : (norme, affichage, debut, fin).
    Les moteurs écrivent parfois « 12 x 8 mm » : on remet la forme parlée, comme pour le compte rendu."""
    brut = []
    for m in mots:
        for norm, aff in jetons(m["mot"], ecrit=True):
            brut.append((norm, aff, float(m["debut"]), float(m["fin"])))
    sortie = []
    for k, (norm, aff, d, f) in enumerate(brut):
        precedent_chiffre = k > 0 and brut[k - 1][0][:1].isdigit()
        suivant_chiffre = k + 1 < len(brut) and brut[k + 1][0][:1].isdigit()
        if norm in {"x", "×"} and precedent_chiffre and suivant_chiffre:
            sortie.append(("fois", "fois", d, f))
        elif aff in _UNITES and precedent_chiffre:
            for mot in _UNITES[aff].split():
                sortie.append((mot.lower(), mot, d, f))
        else:
            sortie.append((norm, aff, d, f))
    return sortie


def _marquer_commandes(normes: list) -> tuple:
    """Renvoie (indices faisant partie d'une commande, indices terminant une phrase)."""
    commande, fin_phrase, i = set(), set(), 0
    while i < len(normes):
        for c in COMMANDES_JETONS:
            if normes[i:i + len(c)] == c:
                commande.update(range(i, i + len(c)))
                if c in FINS_DE_PHRASE:
                    fin_phrase.add(i + len(c) - 1)
                i += len(c) - 1
                break
        i += 1
    return commande, fin_phrase


def _marquer_identifiants(normes: list, noms: set) -> set:
    ident = set()
    for i, n in enumerate(normes):
        if n in CIVILITES:
            ident.update(range(i, min(i + 3, len(normes))))
        if n in noms:
            ident.add(i)
        if n in MOIS:
            ident.update(range(max(0, i - 1), min(i + 2, len(normes))))
        if re.fullmatch(r"(19|20)\d\d", n):
            ident.add(i)
        if n in {"ans", "an"} and i > 0 and normes[i - 1].isdigit():
            ident.update({i - 1, i})
    return ident


def etiqueter(mots_p: list, mots_w: list, compte_rendu: str, noms_propres: frozenset = frozenset()) -> dict:
    """Renvoie {"segments": [...], "concordance": float, "exclus_identifiants": int}."""
    jp = _jetons_mots(mots_p)
    jw = _jetons_mots(mots_w)
    jr, noms = preparer_compte_rendu(compte_rendu)
    pn, wn, rn = [j[0] for j in jp], [j[0] for j in jw], [r[0] for r in jr]
    if not jp:
        return {"segments": [], "concordance": 0.0, "exclus_identifiants": 0}

    p_vers_w = _egalites(pn, wn)                  # jetons Parakeet confirmés par Whisper
    r_confirmes_w = set(_egalites(wn, rn).values())   # jetons du compte rendu entendus par Whisper
    commande, fin_phrase = _marquer_commandes(pn)
    identifiants = _marquer_identifiants(pn, noms)

    # Éléments : (debut, fin, [formes affichées], statut, raison, indices Parakeet)
    elements = []

    def ajouter(indices, formes, statut, raison=""):
        elements.append({"debut": jp[indices[0]][2], "fin": jp[indices[-1]][3], "formes": formes,
                         "statut": statut, "raison": raison, "indices": indices})

    egaux = 0
    # Les opérations « insert » (texte du compte rendu jamais prononcé : modèle, en-tête,
    # reformulation) sont volontairement ignorées : on n'ajoute jamais un mot non dit.
    for op, i1, i2, j1, j2 in _aligner(pn, rn):
        if op == "equal":
            egaux += i2 - i1
            for k in range(i2 - i1):
                ajouter([i1 + k], [jr[j1 + k][1]], "ok")
        elif op == "replace":
            if i2 - i1 == j2 - j1:
                for k in range(i2 - i1):
                    i, j = i1 + k, j1 + k
                    if pn[i] in HESITATIONS:
                        continue
                    if i in commande:
                        ajouter([i], [jp[i][1]], "ok")
                    elif similaires(pn[i], rn[j], SIMILARITE_MOT):
                        ok = j in r_confirmes_w
                        ajouter([i], [jr[j][1]], "ok" if ok else "a_verifier",
                                "" if ok else f"correction « {jp[i][1]} » → « {jr[j][1]} » non confirmée")
                    else:
                        confirme = i in p_vers_w
                        ajouter([i], [jp[i][1]], "ok" if confirme else "a_verifier",
                                "" if confirme else f"désaccord avec le compte rendu (« {jp[i][1]} »)")
            elif similaires(" ".join(pn[i1:i2]), " ".join(rn[j1:j2]), SIMILARITE_BLOC) and not (
                    set(range(i1, i2)) & commande):
                ok = all(j in r_confirmes_w for j in range(j1, j2))
                ajouter(list(range(i1, i2)), [r[1] for r in jr[j1:j2]], "ok" if ok else "a_verifier",
                        "" if ok else "correction de groupe non confirmée")
            else:
                for i in range(i1, i2):
                    _inserer(i, jp, pn, commande, p_vers_w, ajouter)
        elif op == "delete":
            for i in range(i1, i2):
                _inserer(i, jp, pn, commande, p_vers_w, ajouter)

    concordance = egaux / len(pn)
    for e in elements:
        if set(e["indices"]) & identifiants:
            e["statut"], e["raison"] = "identifiant", "identifiant prononcé"
    segments, exclus = _segmenter(elements, fin_phrase, jw, jp, noms_propres)
    if concordance < CONCORDANCE_MIN:
        for s in segments:
            s["statut"] = "a_verifier"
            s["raisons"].append(f"compte rendu peut-être mal apparié (concordance {concordance:.0%})")
    return {"segments": segments, "concordance": round(concordance, 3), "exclus_identifiants": exclus}


def _inserer(i, jp, pn, commande, p_vers_w, ajouter):
    """Jeton Parakeet absent du compte rendu."""
    if pn[i] in HESITATIONS:
        return
    if i in commande:
        ajouter([i], [jp[i][1]], "ok")
    elif i in p_vers_w:
        ajouter([i], [jp[i][1]], "ok")
    else:
        ajouter([i], [jp[i][1]], "a_verifier", f"mot entendu par un seul moteur (« {jp[i][1]} »)")


def _segmenter(elements: list, fin_phrase: set, jw: list, jp: list, noms_propres: frozenset) -> tuple:
    elements = sorted(elements, key=lambda e: (e["debut"], e["indices"][0]))
    groupes, courant = [], []
    for k, e in enumerate(elements):
        if courant:
            duree = e["fin"] - courant[0]["debut"]
            pause = e["debut"] - courant[-1]["fin"]
            fin_prec = courant[-1]["indices"][-1] in fin_phrase
            longueur = courant[-1]["fin"] - courant[0]["debut"]
            frontiere_identifiant = (e["statut"] == "identifiant") != (courant[-1]["statut"] == "identifiant")
            if (frontiere_identifiant or duree > SEG_MAX or (pause >= PAUSE_COUPE and longueur >= 3.0)
                    or (fin_prec and longueur >= SEG_CIBLE)):
                groupes.append(courant)
                courant = []
        courant.append(e)
    if courant:
        groupes.append(courant)

    segments, exclus = [], 0
    for g, groupe in enumerate(groupes):
        if any(e["statut"] == "identifiant" for e in groupe):
            exclus += 1
            continue
        debut = groupe[0]["debut"] - MARGE
        fin = groupe[-1]["fin"] + MARGE
        if g > 0:
            debut = max(debut, groupes[g - 1][-1]["fin"])
        if g + 1 < len(groupes):
            fin = min(fin, groupes[g + 1][0]["debut"])
        debut = max(0.0, debut)
        if fin - debut < SEG_MIN:
            continue
        texte = " ".join(forme(f, noms_propres) for e in groupe for f in e["formes"])
        raisons = [e["raison"] for e in groupe if e["statut"] == "a_verifier"]
        erreurs = [m for n, m in T.verifier_texte(texte, "phrase", noms_propres) if n == "erreur"]
        raisons += [f"convention : {m}" for m in erreurs]
        indices = sorted(i for e in groupe for i in e["indices"])
        segments.append({
            "debut": round(debut, 3), "fin": round(fin, 3), "texte": texte,
            "statut": "a_verifier" if raisons else "ok", "raisons": raisons,
            "texte_parakeet": " ".join(jp[i][1] for i in indices),
            "texte_whisper": " ".join(w[1] for w in jw if w[2] >= debut - 0.05 and w[3] <= fin + 0.05),
        })
    return segments, exclus
