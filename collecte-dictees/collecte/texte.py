"""
Convention d'écriture des transcriptions, correcteur automatique, lexique cible,
couverture des termes et répartition déterministe des phrases.

CONVENTION (identique pour les phrases à lire, les corrections et les manifestes) :
  - on écrit EXACTEMENT les mots prononcés ;
  - les commandes de ponctuation dictées s'écrivent EN TOUTES LETTRES
    (« virgule », « point à la ligne »...) : c'est le formatage vocal de RadIA qui les convertit ;
  - aucun signe de ponctuation (. ; : ! ? ( ) / « ») ; la virgule n'est admise qu'entre deux
    chiffres (3,5) ; trait d'union et apostrophe sont admis ;
  - nombres en chiffres arabes (12, 3,5, segment 7, stade 4), unités en toutes lettres
    (millimètres), « fois » entre deux dimensions (12 fois 8) ;
  - minuscules, sauf sigles (IRM, BI-RADS, T2) et noms propres du lexique (Bosniak).
"""
import csv
import hashlib
import io
import random
import re
import unicodedata
from pathlib import Path

# Commandes dictées : à aligner sur la liste du formatage vocal de RadIA
# (src/lib/dictation/voice-commands). Les plus longues d'abord.
COMMANDES = [
    "point à la ligne", "nouveau paragraphe", "ouvrez la parenthèse", "fermez la parenthèse",
    "point-virgule", "deux points", "à la ligne", "virgule", "tiret", "point",
]

TYPES = {"phrase", "compte_rendu"}
MOTS_MAX = {"phrase": 40, "compte_rendu": 450}

_UNITES_ABREGEES = re.compile(r"(?<![\w-])(mm|cm|ml|mL|cc|kg|mg|mGy|UH|HU|mm2|mm3|cm2|cm3)(?![\w-])")
_DIMENSION_X = re.compile(r"\d\s*[x×]\s*\d")
_DECIMALE_POINT = re.compile(r"\d\.\d")
_SYMBOLES = re.compile(r"[.;:!?()\[\]{}\"«»…/\\]")
_VIRGULE_HORS_CHIFFRES = re.compile(r"(?<!\d),|,(?!\d)")
_ROMAINS = re.compile(r"(?<![\w-])[IVXL]{2,}(?![\w-])")
_NOMBRES_LETTRES = ("deux|trois|quatre|cinq|six|sept|huit|neuf|dix|onze|douze|treize|quatorze|"
                    "quinze|seize|vingt|trente|quarante|cinquante|soixante|cent|mille")
_UNITES_LETTRES = "millimètres?|centimètres?|millilitres?|mètres?|grammes?|fois"
_NOMBRE_ECRIT = re.compile(rf"(?<!\w)({_NOMBRES_LETTRES})\s+({_UNITES_LETTRES})(?!\w)"
                           rf"|(?<!\w)fois\s+({_NOMBRES_LETTRES})(?!\w)", re.IGNORECASE)
_DATE = re.compile(r"\b\d{1,2}\s*[/.-]\s*\d{1,2}\s*[/.-]\s*\d{2,4}\b"
                   r"|\b\d{1,2}\s+(janvier|février|mars|avril|mai|juin|juillet|août|septembre|"
                   r"octobre|novembre|décembre)\b", re.IGNORECASE)
_CIVILITE = re.compile(r"(?<!\w)(M|Mme|Mlle|Monsieur|Madame|Mademoiselle|Dr|Docteur|Pr|Professeur)(?!\w)")
_ID = re.compile(r"^[A-Za-z0-9_-]+$")


# ─────────────────────────── Normalisation ───────────────────────────

def normaliser(texte: str) -> str:
    """Forme de comparaison : minuscules, sans ponctuation (sauf virgule décimale), espaces simples."""
    t = unicodedata.normalize("NFC", texte).replace("’", "'").lower()
    t = re.sub(r"(?<=\d),(?=\d)", "§", t)          # protège la virgule décimale
    t = re.sub(r"[^\w\s'§-]", " ", t)
    return " ".join(t.replace("§", ",").split())


def sans_commandes(texte_normalise: str) -> str:
    t = f" {texte_normalise} "
    for c in COMMANDES:
        t = t.replace(f" {c} ", " ")
    return " ".join(t.split())


def contient(texte_normalise: str, terme_normalise: str) -> bool:
    fin = "" if terme_normalise.endswith("'") else r"(?![\w])"   # « pas d' » est suivi d'une lettre
    return re.search(rf"(?<![\w']){re.escape(terme_normalise)}{fin}", texte_normalise) is not None


# ─────────────────────────── Correcteur ───────────────────────────

def verifier_texte(texte: str, type_: str = "phrase", noms_propres: frozenset = frozenset()) -> list:
    """Renvoie une liste de (niveau, message). niveau = 'erreur' (bloquant) ou 'avertissement'."""
    pb = []
    t = texte.strip()
    if not t:
        return [("erreur", "texte vide")]
    if _UNITES_ABREGEES.search(t):
        pb.append(("erreur", "unité abrégée : écrire l'unité en toutes lettres (millimètres)"))
    if _DIMENSION_X.search(t):
        pb.append(("erreur", "« x » entre deux dimensions : écrire « 12 fois 8 »"))
    if _DECIMALE_POINT.search(t):
        pb.append(("erreur", "décimale avec un point : écrire « 3,5 »"))
    if _SYMBOLES.search(t):
        pb.append(("erreur", "signe de ponctuation : écrire la commande dictée en toutes lettres "
                             "(« point », « deux points », « ouvrez la parenthèse »)"))
    if _VIRGULE_HORS_CHIFFRES.search(t):
        pb.append(("erreur", "virgule hors d'un nombre : écrire « virgule » si elle est dictée"))
    if _ROMAINS.search(t):
        pb.append(("erreur", "chiffres romains : écrire en chiffres arabes (segment 7, stade 4)"))
    if _NOMBRE_ECRIT.search(t):
        pb.append(("erreur", "nombre écrit en lettres : écrire en chiffres (12 millimètres)"))
    if _DATE.search(t):
        pb.append(("erreur", "date : retirer toute date (risque d'identification)"))
    if _CIVILITE.search(t):
        pb.append(("erreur", "civilité ou titre (M., Dr...) : risque d'identification d'une personne"))
    mots = t.split()
    if len(mots) > MOTS_MAX.get(type_, 40):
        pb.append(("erreur", f"trop long pour le type « {type_} » ({len(mots)} mots, "
                             f"maximum {MOTS_MAX.get(type_, 40)})"))
    for m in mots:
        nu = m.strip("'-")
        if any(c.isupper() for c in nu) and not any(c.isdigit() for c in nu) \
                and not nu.isupper() and nu.lower() not in noms_propres:
            pb.append(("avertissement", f"majuscule sur « {nu} » : nom propre ? Si c'est un éponyme, "
                                        "l'ajouter au lexique ; sinon mettre en minuscules"))
    return pb


# ─────────────────────────── Banque de phrases et lexique ───────────────────────────

def lire_banque(contenu: bytes) -> list:
    """Lit phrases.csv (id, type, modalite, texte)."""
    phrases = []
    for ligne in csv.DictReader(io.StringIO(contenu.decode("utf-8-sig"))):
        phrases.append({
            "id": (ligne.get("id") or "").strip(),
            "type": (ligne.get("type") or "phrase").strip(),
            "modalite": (ligne.get("modalite") or "Autre").strip(),
            "texte": " ".join((ligne.get("texte") or "").split()),
        })
    return phrases


def lire_lexique(contenu: bytes) -> list:
    termes = []
    for ligne in csv.DictReader(io.StringIO(contenu.decode("utf-8-sig"))):
        terme = (ligne.get("terme") or "").strip()
        if terme:
            termes.append({"terme": terme, "categorie": (ligne.get("categorie") or "").strip(),
                           "norme": normaliser(terme)})
    return termes


def noms_propres_du_lexique(lexique: list) -> frozenset:
    return frozenset(l["terme"].lower() for l in lexique
                     if l["terme"][:1].isupper() and not l["terme"].isupper())


def verifier_banque(phrases: list, lexique: list) -> list:
    """Vérifie toute la banque. Renvoie des (niveau, id, message)."""
    pb = []
    noms = noms_propres_du_lexique(lexique)
    ids, textes = {}, {}
    for p in phrases:
        pid = p["id"] or "(sans id)"
        if not _ID.match(p["id"]):
            pb.append(("erreur", pid, "identifiant invalide (lettres, chiffres, _ ou -)"))
        if p["id"] in ids:
            pb.append(("erreur", pid, "identifiant en double"))
        ids[p["id"]] = True
        if p["type"] not in TYPES:
            pb.append(("erreur", pid, f"type inconnu « {p['type']} » (phrase ou compte_rendu)"))
        cle = normaliser(p["texte"])
        if cle in textes:
            pb.append(("erreur", pid, f"même texte que {textes[cle]}"))
        textes[cle] = pid
        for niveau, msg in verifier_texte(p["texte"], p["type"], noms):
            pb.append((niveau, pid, msg))
    for terme in lexique:
        if not any(contient(normaliser(p["texte"]), terme["norme"]) for p in phrases):
            pb.append(("avertissement", terme["terme"], "terme du lexique présent dans aucune phrase"))
    return pb


def version_banque(contenu: bytes) -> str:
    return hashlib.sha256(contenu).hexdigest()[:12]


def termes_de(texte: str, lexique: list) -> set:
    n = normaliser(texte)
    return {l["terme"] for l in lexique if contient(n, l["norme"])}


# ─────────────────────────── Répartition et sélection ───────────────────────────

def ensemble_de(id_phrase: str) -> str:
    """Répartition DÉTERMINISTE par identifiant de phrase : 80 % train, 10 % val, 10 % test.
    Une phrase garde toujours le même ensemble, donc le test ne bouge jamais
    (tant qu'on ne réutilise pas un identifiant)."""
    h = int(hashlib.sha1(id_phrase.encode("utf-8")).hexdigest(), 16) % 10
    return "test" if h == 0 else "val" if h == 1 else "train"


def choisir_phrase(restantes: list, couverture: dict, termes_par_id: dict, cible: int, graine: str):
    """Choisit la phrase qui apporte le plus aux termes les moins couverts.
    couverture : terme -> nombre de voix distinctes l'ayant déjà prononcé."""
    if not restantes:
        return None
    rng = random.Random(graine)

    def score(p):
        gain = sum(max(0, cible - couverture.get(t, 0)) for t in termes_par_id.get(p["id"], ()))
        return (gain, rng.random())

    return max(restantes, key=score)


def surligner_commandes(texte_echappe: str) -> str:
    """Entoure les commandes dictées d'un <span class='cmd'> (texte déjà échappé en HTML)."""
    motif = "|".join(re.escape(c) for c in COMMANDES)
    return re.sub(rf"(?<![\w'-])({motif})(?![\w'-])", r"<span class='cmd'>\1</span>", texte_echappe)
