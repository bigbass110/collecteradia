"""
Traitement par lots des dictées importées (exécuté sur GPU, par gpu/modal_etiquetage.py).

Pour chaque dictée importée pas encore traitée :
  transcription Parakeet + Whisper -> étiquetage (collecte/etiquetage.py) -> segments écrits au format
  de l'application : brut/hist_<id>_sNNN.wav/.txt, journaux/hist_<code>.json, validations/resultats.json.
L'état de chaque dictée est écrit dans historique/etat/<code>.json, et l'avancement global dans
historique/travaux/etat.json (lu par l'application).
"""
import io
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import soundfile as sf

from . import VERSION_APP
from . import audio as A
from . import etiquetage as E
from . import jeux as J
from . import texte as T
from .importation import etat_de, index_de
from .stockage import ecrire_json, lire_json

CLE_TRAVAIL = "historique/travaux/etat.json"
DELAI_VERROU_S = 20 * 60


def maintenant() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def travail_en_cours(stockage) -> bool:
    etat = lire_json(stockage, CLE_TRAVAIL, {})
    if etat.get("statut") != "en_cours" or not etat.get("maj"):
        return False
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(etat["maj"])).total_seconds()
    return age < DELAI_VERROU_S


def ecart_caracteres(a: str, b: str) -> float:
    """CER entre deux textes normalisés (distance d'édition / longueur de a)."""
    a, b = T.normaliser(a), T.normaliser(b)
    if not a:
        return 1.0 if b else 0.0
    precedent = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        courant = [i]
        for j, cb in enumerate(b, 1):
            courant.append(min(precedent[j] + 1, courant[j - 1] + 1, precedent[j - 1] + (ca != cb)))
        precedent = courant
    return precedent[-1] / len(a)


def dictees_en_attente(stockage, relancer_erreurs: bool) -> list:
    attente = []
    for cle in stockage.lister("historique/index/"):
        code = cle.split("/")[-1][:-5]
        etat = etat_de(stockage, code)
        for e in index_de(stockage, code):
            s = etat.get(e["id"], {}).get("statut")
            if s is None or s == "en_cours" or (relancer_erreurs and s == "erreur"):
                attente.append(e)
    return attente


def traiter(stockage, transcrire_parakeet, transcrire_whisper, test_locuteurs: set,
            noms_propres: frozenset = frozenset(), relancer_erreurs: bool = False, id_travail: str = "") -> dict:
    """transcrire_* : fonction(chemin_wav, modalite) -> [{"mot", "debut", "fin"}]."""
    if J.validation_en_cours(stockage):
        motif = "une validation (valider_enregistrements.py) est en cours : relancez l'étiquetage quand elle sera terminée"
        ecrire_json(stockage, CLE_TRAVAIL, {"id_travail": id_travail, "statut": "refuse", "motif": motif,
                                            "maj": maintenant()})      # libère l'état « en cours »
        raise RuntimeError(motif)
    attente = dictees_en_attente(stockage, relancer_erreurs)
    bilan = {"id_travail": id_travail, "statut": "en_cours", "debut": maintenant(), "maj": maintenant(),
             "total": len(attente), "traitees": 0, "segments": 0, "a_verifier": 0, "erreurs": 0}
    ecrire_json(stockage, CLE_TRAVAIL, bilan)

    for d in attente:
        code, ident = d["code"], d["id"]
        etat = etat_de(stockage, code)
        try:
            wav = stockage.lire(f"historique/audio/{ident}.wav")
            cr = (stockage.lire(f"historique/cr/{ident}.txt") or b"").decode("utf-8")
            with tempfile.TemporaryDirectory() as dossier:
                chemin = str(Path(dossier) / f"{ident}.wav")
                Path(chemin).write_bytes(wav)
                mots_p = transcrire_parakeet(chemin, d.get("modalite", ""))
                mots_w = transcrire_whisper(chemin, d.get("modalite", ""))
            res = E.etiqueter(mots_p, mots_w, cr, noms_propres)
            audio, _ = sf.read(io.BytesIO(wav), dtype="float32")

            cle_journal = f"journaux/hist_{code}.json"
            journal = lire_json(stockage, cle_journal, [])
            retires, nouveaux = [], {}
            for ancien in [j for j in journal if j.get("groupe") == ident]:   # nouvelle passe : on remplace
                stockage.supprimer(ancien["fichier"])
                stockage.supprimer(ancien["fichier"].replace(".wav", ".txt"))
                retires.append(ancien["fichier"])
            journal = [j for j in journal if j.get("groupe") != ident]

            reserve_test = code in test_locuteurs or T.ensemble_de(ident) == "test"
            nb_verif = 0
            for n, s in enumerate(res["segments"], 1):
                fichier = f"brut/hist_{ident}_s{n:03d}.wav"
                morceau = audio[int(s["debut"] * A.FREQ): int(s["fin"] * A.FREQ)]
                stockage.ecrire(fichier, A.en_wav(morceau))
                stockage.ecrire(fichier.replace(".wav", ".txt"), s["texte"].encode("utf-8"))
                raisons = list(s["raisons"])
                if reserve_test:
                    raisons.append("réservé au test : relecture intégrale")
                statut = "a_verifier" if raisons else "ok"
                nb_verif += statut == "a_verifier"
                journal.append({
                    "horodatage": maintenant(), "code": code, "id_phrase": f"{ident}_s{n:03d}",
                    "groupe": ident, "type": "dictee_reelle", "source": "historique", "journal": cle_journal,
                    "modalite": d.get("modalite", ""), "fichier": fichier,
                    "duree_s": round(s["fin"] - s["debut"], 2), "texte_attendu": s["texte"],
                    "texte_final": s["texte"], "texte_modifie": False, "alertes": "",
                    "texte_parakeet": s["texte_parakeet"], "texte_whisper": s["texte_whisper"],
                    "micro": "", "lieu": "Pratique réelle", "version_app": VERSION_APP,
                })
                nouveaux[fichier] = {
                    "statut": statut, "raisons": raisons,
                    "cer": round(ecart_caracteres(s["texte_parakeet"], s["texte_whisper"]), 4),
                    "hypothese": f"Parakeet : {s['texte_parakeet']} · Whisper : {s['texte_whisper']}",
                    "modele": "parakeet-tdt-0.6b-v3 + whisper-large-v3", "horodatage": maintenant(),
                }
            ecrire_json(stockage, cle_journal, journal)
            J.maj_resultats(stockage, nouveaux, retires)
            etat[ident] = {"statut": "etiquete", "segments": len(res["segments"]), "a_verifier": nb_verif,
                           "exclus_identifiants": res["exclus_identifiants"],
                           "ponctuation_moteur": res.get("ponctuation_moteur", 0),
                           "concordance": res["concordance"], "maj": maintenant()}
            bilan["segments"] += len(res["segments"])
            bilan["a_verifier"] += nb_verif
        except Exception as erreur:   # une dictée en échec n'arrête pas le lot
            etat[ident] = {"statut": "erreur", "erreur": f"{type(erreur).__name__} : {erreur}"[:300],
                           "maj": maintenant()}
            bilan["erreurs"] += 1
        ecrire_json(stockage, f"historique/etat/{code}.json", etat)
        bilan["traitees"] += 1
        bilan["maj"] = maintenant()
        ecrire_json(stockage, CLE_TRAVAIL, bilan)

    bilan["statut"], bilan["fin"] = "termine", maintenant()
    ecrire_json(stockage, CLE_TRAVAIL, bilan)
    return bilan
