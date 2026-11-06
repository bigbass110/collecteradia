"""
Programme GPU (Modal) : étiquette les dictées importées depuis l'application.

Déploiement (une fois, depuis la racine du dépôt) :
    pip install modal
    modal setup                                   # relie votre compte Modal
    modal secret create radia-etiquetage \
        R2_ACCOUNT_ID=... R2_ACCESS_KEY_ID=... R2_SECRET_ACCESS_KEY=... R2_BUCKET=radia-collecte \
        RADIA_JETON=<longue-chaine-aleatoire>
    modal deploy gpu/modal_etiquetage.py

Modal affiche l'adresse du point d'entrée « lancer » : copiez-la dans les Secrets de l'application
(MODAL_URL), avec le même RADIA_JETON (MODAL_JETON).

Test manuel sans l'application :
    curl -X POST <MODAL_URL> -H "Authorization: Bearer <RADIA_JETON>" \
         -H "Content-Type: application/json" -d '{"test_locuteurs": [], "relancer_erreurs": false}'
"""
import secrets as _secrets

import modal

app = modal.App("radia-etiquetage")
SECRET = modal.Secret.from_name("radia-etiquetage")
PARAKEET = "nvidia/parakeet-tdt-0.6b-v3"
WHISPER = "large-v3"


def _telecharger_modeles():
    """Exécuté à la construction de l'image : les modèles sont prêts au démarrage."""
    import nemo.collections.asr as nemo_asr
    from faster_whisper import WhisperModel
    nemo_asr.models.ASRModel.from_pretrained(PARAKEET)
    WhisperModel(WHISPER, device="cpu", compute_type="int8")


image_gpu = (
    modal.Image.from_registry("nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04", add_python="3.12")
    .apt_install("ffmpeg", "git", "build-essential")
    .pip_install("nemo_toolkit[asr]", "faster-whisper", "boto3", "soundfile", "numpy")
    .run_function(_telecharger_modeles)
    .add_local_python_source("collecte")
    .add_local_file("lexique.csv", "/root/lexique.csv")
)

image_legere = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("fastapi[standard]", "boto3", "numpy", "soundfile")
    .add_local_python_source("collecte")
)

with image_legere.imports():   # importé dans le conteneur ; ignoré sur votre PC s'il manque
    from fastapi import HTTPException, Request


@app.function(image=image_gpu, gpu="L4", timeout=6 * 3600, secrets=[SECRET])
def etiqueter(id_travail: str, test_locuteurs: list, relancer_erreurs: bool):
    from pathlib import Path

    import nemo.collections.asr as nemo_asr
    from faster_whisper import WhisperModel

    from collecte import etiquetage as E
    from collecte import texte as T
    from collecte import travail
    from collecte.stockage import depuis_environnement

    lexique = T.lire_lexique(Path("/root/lexique.csv").read_bytes())
    noms = T.noms_propres_du_lexique(lexique)

    parakeet = nemo_asr.models.ASRModel.from_pretrained(PARAKEET)
    whisper = WhisperModel(WHISPER, device="cuda", compute_type="float16")

    def transcrire_parakeet(chemin, modalite):
        import soundfile as sf
        if sf.info(chemin).duration > 20 * 60:     # très longues dictées : attention locale
            parakeet.change_attention_model("rel_pos_local_attn", [256, 256])
        sortie = parakeet.transcribe([chemin], timestamps=True)[0]
        return [{"mot": w["word"], "debut": w["start"], "fin": w["end"]} for w in sortie.timestamp["word"]]

    def transcrire_whisper(chemin, modalite):
        # Pas de vocabulaire dans l'invite : Whisper doit rester une source INDÉPENDANTE pour confirmer
        # les corrections du compte rendu (voir etiquetage.invite_whisper).
        segments, _ = whisper.transcribe(chemin, language="fr", word_timestamps=True,
                                         vad_filter=True, initial_prompt=E.invite_whisper(modalite))
        return [{"mot": w.word, "debut": w.start, "fin": w.end} for s in segments for w in (s.words or [])]

    return travail.traiter(depuis_environnement(), transcrire_parakeet, transcrire_whisper,
                           set(test_locuteurs), noms, relancer_erreurs, id_travail)


@app.function(image=image_legere, secrets=[SECRET])
@modal.fastapi_endpoint(method="POST")
def lancer(corps: dict, request: Request):
    import hmac
    import os

    from collecte import travail
    from collecte.stockage import depuis_environnement, ecrire_json

    attendu = os.environ["RADIA_JETON"]
    fourni = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(fourni.encode(), attendu.encode()):
        raise HTTPException(status_code=403, detail="jeton invalide")
    stockage = depuis_environnement()
    if travail.travail_en_cours(stockage):
        raise HTTPException(status_code=409, detail="un étiquetage est déjà en cours")
    id_travail = _secrets.token_hex(4)
    ecrire_json(stockage, travail.CLE_TRAVAIL,
                {"id_travail": id_travail, "statut": "en_cours", "debut": travail.maintenant(),
                 "maj": travail.maintenant(), "total": None, "traitees": 0})
    etiqueter.spawn(id_travail, list(corps.get("test_locuteurs", [])), bool(corps.get("relancer_erreurs")))
    return {"id_travail": id_travail}
