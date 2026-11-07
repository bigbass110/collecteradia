"""
Import de dictées historiques (audio + compte rendu) depuis l'application web.

Stockage :
  historique/audio/<id>.wav        audio mono 16 kHz
  historique/cr/<id>.txt           texte du compte rendu
  historique/index/<code>.json     dictées importées pour ce radiologue (écrit par l'application)
  historique/etat/<code>.json      état d'étiquetage (écrit par le programme GPU ; l'application n'y retire
                                   une dictée que lorsqu'elle est supprimée ou corrigée, hors étiquetage)
Le nom de fichier d'origine n'est jamais conservé (il contient souvent le nom du patient).
"""
import hashlib
import io
import secrets
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePath
from xml.etree import ElementTree

from . import etiquetage as E
from . import jeux as J
from .stockage import ecrire_json, lire_json

EXT_AUDIO = {".wav", ".mp3", ".m4a", ".mp4", ".aac", ".ogg", ".opus", ".webm", ".flac", ".amr", ".3gp", ".dss", ".wma"}
EXT_TEXTE = {".txt", ".docx", ".pdf", ".rtf", ".odt", ".doc"}
EXT_ARCHIVE = {".zip"}
DUREE_MIN, DUREE_MAX = 5.0, 1800.0
MAX_LOT = 20
ZIP_MAX_FICHIERS, ZIP_MAX_OCTETS = 200, 1_000_000_000


@dataclass
class Fichier:
    """Fichier extrait d'une archive, avec la même interface que les fichiers déposés dans Streamlit."""
    name: str
    donnees: bytes

    def getvalue(self) -> bytes:
        return self.donnees


def deballer(fichiers: list) -> tuple:
    """Remplace chaque .zip par les fichiers qu'il contient (dossiers, fichiers cachés et __MACOSX ignorés).
    Renvoie (fichiers, problèmes)."""
    sortie, problemes = [], []
    for f in fichiers:
        if PurePath(f.name).suffix.lower() not in EXT_ARCHIVE:
            sortie.append(f)
            continue
        try:
            with zipfile.ZipFile(io.BytesIO(f.getvalue())) as z:
                membres = [m for m in z.infolist() if not m.is_dir() and not m.filename.startswith("__MACOSX/")
                           and not PurePath(m.filename).name.startswith(".")]
                if len(membres) > ZIP_MAX_FICHIERS or sum(m.file_size for m in membres) > ZIP_MAX_OCTETS:
                    problemes.append(f"{f.name} : archive trop grosse (au plus {ZIP_MAX_FICHIERS} fichiers "
                                     f"et {ZIP_MAX_OCTETS // 10**9} Go une fois décompressée)")
                    continue
                sortie += [Fichier(PurePath(m.filename).name, z.read(m)) for m in membres]
        except zipfile.BadZipFile:
            problemes.append(f"{f.name} : archive .zip illisible")
    return sortie, problemes


def apparier(fichiers: list) -> tuple:
    """fichiers : objets ayant .name. Associe audio et compte rendu par nom (sans extension, casse ignorée).
    Renvoie (paires [(audio, texte)], problèmes [messages])."""
    paires, audios_seuls, textes_seuls, problemes = apparier_detail(fichiers)
    problemes += [f"{a.name} : compte rendu manquant" for a in audios_seuls]
    problemes += [f"{t.name} : audio manquant" for t in textes_seuls]
    return paires, problemes


def apparier_detail(fichiers: list) -> tuple:
    """Comme apparier, mais renvoie aussi les fichiers restés seuls, pour un appariement manuel :
    (paires, audios sans compte rendu, comptes rendus sans audio, problèmes)."""
    audios, textes, problemes = {}, {}, []
    for f in fichiers:
        p = PurePath(f.name)
        cle, ext = p.stem.strip().lower(), p.suffix.lower()
        cible = audios if ext in EXT_AUDIO else textes if ext in EXT_TEXTE else None
        if cible is None:
            problemes.append(f"{f.name} : format non pris en charge")
        elif cle in cible:
            problemes.append(f"{f.name} : deux fichiers portent le même nom")
        else:
            cible[cle] = f
    paires = [(audios[c], textes[c]) for c in sorted(set(audios) & set(textes))]
    return (paires, [audios[c] for c in sorted(set(audios) - set(textes))],
            [textes[c] for c in sorted(set(textes) - set(audios))], problemes)


def extraire_texte(nom: str, contenu: bytes) -> str:
    ext = PurePath(nom).suffix.lower()
    if ext == ".txt":
        for codage in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                texte = contenu.decode(codage)
                break
            except UnicodeDecodeError:
                continue
    elif ext == ".docx":
        from docx import Document
        doc = Document(io.BytesIO(contenu))
        lignes = [p.text for p in doc.paragraphs]
        for table in doc.tables:
            for rangee in table.rows:
                lignes.append(" ".join(c.text for c in rangee.cells))
        texte = "\n".join(lignes)
    elif ext == ".pdf":
        from pypdf import PdfReader
        texte = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(contenu)).pages)
    elif ext == ".rtf":
        from striprtf.striprtf import rtf_to_text
        texte = rtf_to_text(contenu.decode("latin-1"), encoding="cp1252", errors="ignore")
    elif ext == ".odt":
        texte = _texte_odt(contenu)
    elif ext == ".doc":
        texte = _texte_doc(contenu)
    else:
        raise ValueError(f"format de compte rendu non pris en charge : {ext}")
    lignes = [" ".join(l.split()) for l in texte.splitlines()]
    return "\n".join(l for l in lignes if l)


def _texte_odt(contenu: bytes) -> str:
    """Texte d'un document OpenDocument (LibreOffice) : un paragraphe ou un titre par ligne."""
    ns = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
    with zipfile.ZipFile(io.BytesIO(contenu)) as z:
        racine = ElementTree.fromstring(z.read("content.xml"))
    return "\n".join("".join(e.itertext()) for e in racine.iter() if e.tag in (f"{ns}p", f"{ns}h"))


def _texte_doc(contenu: bytes) -> str:
    """Texte d'un ancien document Word (.doc), avec antiword s'il est installé sur le serveur."""
    if not shutil.which("antiword"):
        raise ValueError("format .doc non lisible sur ce serveur : enregistrez le compte rendu en .docx "
                         "(Word → Enregistrer sous)")
    with tempfile.NamedTemporaryFile(suffix=".doc") as f:
        f.write(contenu)
        f.flush()
        return subprocess.run(["antiword", "-m", "UTF-8.txt", f.name], capture_output=True, check=True,
                              timeout=60).stdout.decode("utf-8", errors="replace")


def apercu_compte_rendu(texte: str) -> tuple:
    """(lignes utilisées, lignes d'en-tête ignorées à l'étiquetage), comme le fait etiquetage.py."""
    gardees, retirees = [], []
    for ligne in texte.splitlines():
        (retirees if E.MOTS_ENTETE.search(ligne) else gardees).append(ligne)
    return gardees, retirees


def index_de(stockage, code: str) -> list:
    return lire_json(stockage, f"historique/index/{code}.json", [])


def etat_de(stockage, code: str) -> dict:
    return lire_json(stockage, f"historique/etat/{code}.json", {})


def enregistrer(stockage, code: str, wav: bytes, duree_s: float, texte_cr: str, modalite: str,
                importe_par: str) -> tuple:
    """Enregistre une dictée. Renvoie (id, None) ou (None, motif du refus)."""
    if not texte_cr.strip():
        return None, "compte rendu vide (PDF scanné ? il faut un PDF contenant du texte)"
    if duree_s < DUREE_MIN:
        return None, f"audio trop court ({duree_s:.0f} s)"
    if duree_s > DUREE_MAX:
        return None, f"audio trop long ({duree_s / 60:.0f} min, maximum {DUREE_MAX / 60:.0f} min)"
    empreinte = hashlib.sha256(wav).hexdigest()
    index = index_de(stockage, code)
    if any(e["empreinte"] == empreinte for e in index):
        return None, "déjà importé"
    ident = "h" + secrets.token_hex(6)
    stockage.ecrire(f"historique/audio/{ident}.wav", wav)
    stockage.ecrire(f"historique/cr/{ident}.txt", texte_cr.encode("utf-8"))
    index.append({"id": ident, "code": code, "duree_s": round(duree_s, 1), "modalite": modalite,
                  "empreinte": empreinte, "importe_par": importe_par,
                  "importe_le": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    ecrire_json(stockage, f"historique/index/{code}.json", index)
    return ident, None


def _retirer_segments(stockage, code: str, ident: str) -> int:
    """Supprime les segments déjà étiquetés d'une dictée, et leurs résultats et décisions."""
    cle_journal = f"journaux/hist_{code}.json"
    journal = lire_json(stockage, cle_journal, [])
    segments = [j["fichier"] for j in journal if j.get("groupe") == ident]
    for f in segments:
        stockage.supprimer(f)
        stockage.supprimer(f.replace(".wav", ".txt"))
    if segments:
        ecrire_json(stockage, cle_journal, [j for j in journal if j.get("groupe") != ident])
        J.maj_resultats(stockage, retires=segments)
        decisions = lire_json(stockage, J.CLE_DECISIONS, {})
        ecrire_json(stockage, J.CLE_DECISIONS, {f: v for f, v in decisions.items() if f not in set(segments)})
    etat = etat_de(stockage, code)
    if etat.pop(ident, None) is not None:
        ecrire_json(stockage, f"historique/etat/{code}.json", etat)
    return len(segments)


def supprimer_dictee(stockage, code: str, ident: str) -> int:
    """Supprime une dictée importée (audio, compte rendu, segments). Renvoie le nombre de segments retirés.
    À n'appeler que hors étiquetage GPU."""
    n = _retirer_segments(stockage, code, ident)
    stockage.supprimer(f"historique/audio/{ident}.wav")
    stockage.supprimer(f"historique/cr/{ident}.txt")
    ecrire_json(stockage, f"historique/index/{code}.json", [e for e in index_de(stockage, code) if e["id"] != ident])
    return n


def modifier_dictee(stockage, code: str, ident: str, modalite: str = None, texte_cr: str = None) -> str:
    """Change la modalité et/ou remplace le compte rendu d'une dictée importée. Un nouveau compte rendu
    efface les segments déjà étiquetés : la dictée repasse « en attente d'étiquetage ».
    Renvoie un message de refus, ou "" si tout est fait. À n'appeler que hors étiquetage GPU."""
    index = index_de(stockage, code)
    entree = next((e for e in index if e["id"] == ident), None)
    if entree is None:
        return "dictée introuvable"
    if texte_cr is not None:
        if not texte_cr.strip():
            return "compte rendu vide"
        _retirer_segments(stockage, code, ident)
        stockage.ecrire(f"historique/cr/{ident}.txt", texte_cr.encode("utf-8"))
        entree["cr_modifie_le"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if modalite is not None:
        entree["modalite"] = modalite
        cle_journal = f"journaux/hist_{code}.json"
        journal = lire_json(stockage, cle_journal, [])
        if any(j.get("groupe") == ident for j in journal):
            for j in journal:
                if j.get("groupe") == ident:
                    j["modalite"] = modalite
            ecrire_json(stockage, cle_journal, journal)
    ecrire_json(stockage, f"historique/index/{code}.json", index)
    return ""


def supprimer_code(stockage, code: str) -> int:
    """Retrait de consentement : supprime tout l'historique d'un radiologue et ses segments."""
    n = 0
    for e in index_de(stockage, code):
        for cle in (f"historique/audio/{e['id']}.wav", f"historique/cr/{e['id']}.txt"):
            stockage.supprimer(cle)
            n += 1
    for j in lire_json(stockage, f"journaux/hist_{code}.json", []):
        stockage.supprimer(j["fichier"])
        n += 1
    for cle in (f"historique/index/{code}.json", f"historique/etat/{code}.json", f"journaux/hist_{code}.json"):
        stockage.supprimer(cle)
    return n
