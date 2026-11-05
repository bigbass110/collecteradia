"""
Import de dictées historiques (audio + compte rendu) depuis l'application web.

Stockage :
  historique/audio/<id>.wav        audio mono 16 kHz
  historique/cr/<id>.txt           texte du compte rendu
  historique/index/<code>.json     dictées importées pour ce radiologue (écrit par l'application)
  historique/etat/<code>.json      état d'étiquetage (écrit UNIQUEMENT par le programme GPU)
Le nom de fichier d'origine n'est jamais conservé (il contient souvent le nom du patient).
"""
import hashlib
import io
import secrets
from datetime import datetime, timezone
from pathlib import PurePath

from .stockage import ecrire_json, lire_json

EXT_AUDIO = {".wav", ".mp3", ".m4a", ".mp4", ".aac", ".ogg", ".opus", ".webm", ".flac", ".amr", ".3gp", ".dss", ".wma"}
EXT_TEXTE = {".txt", ".docx", ".pdf"}
DUREE_MIN, DUREE_MAX = 5.0, 1800.0
MAX_LOT = 20


def apparier(fichiers: list) -> tuple:
    """fichiers : objets ayant .name. Associe audio et compte rendu par nom (sans extension, casse ignorée).
    Renvoie (paires [(audio, texte)], problèmes [messages])."""
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
    for cle in sorted(set(audios) - set(textes)):
        problemes.append(f"{audios[cle].name} : compte rendu manquant")
    for cle in sorted(set(textes) - set(audios)):
        problemes.append(f"{textes[cle].name} : audio manquant")
    paires = [(audios[c], textes[c]) for c in sorted(set(audios) & set(textes))]
    return paires, problemes


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
    else:
        raise ValueError(f"format de compte rendu non pris en charge : {ext}")
    lignes = [" ".join(l.split()) for l in texte.splitlines()]
    return "\n".join(l for l in lignes if l)


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
