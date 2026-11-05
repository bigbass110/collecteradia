"""Conversion, rognage des silences et contrôles qualité des enregistrements."""
import io
import shutil
import subprocess

import numpy as np
import soundfile as sf

FREQ = 16_000
TRAME = 320                 # 20 ms à 16 kHz
MARGE_S = 0.25              # silence conservé avant et après la parole
RMS_MIN = 0.005
LIMITES = {"phrase": (1.0, 20.0), "compte_rendu": (10.0, 360.0)}


def convertir_16k(brut: bytes) -> np.ndarray:
    """Audio du navigateur -> mono 16 kHz float32. ffmpeg si disponible (recommandé)."""
    if shutil.which("ffmpeg"):
        sortie = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
             "-ac", "1", "-ar", str(FREQ), "-f", "wav", "pipe:1"],
            input=brut, capture_output=True, check=True).stdout
        audio, _ = sf.read(io.BytesIO(sortie), dtype="float32")
        return audio
    audio, freq = sf.read(io.BytesIO(brut), dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if freq != FREQ:
        n = int(len(audio) * FREQ / freq)
        audio = np.interp(np.linspace(0, len(audio), n, endpoint=False),
                          np.arange(len(audio)), audio).astype("float32")
    return audio


def _energies(audio: np.ndarray) -> np.ndarray:
    n = len(audio) // TRAME
    if n == 0:
        return np.zeros(0)
    trames = audio[: n * TRAME].reshape(n, TRAME)
    return np.sqrt(np.mean(trames ** 2, axis=1))


def rogner(audio: np.ndarray) -> tuple:
    """Retire les silences de début et de fin en gardant MARGE_S de marge.
    Renvoie (audio_rogné, début_coupé, fin_coupée) : un enregistrement où la parole commence
    ou finit dans les 60 premières/dernières millisecondes a probablement été coupé."""
    e = _energies(audio)
    if len(e) == 0:
        return audio, False, False
    bruit = float(np.percentile(e, 10))
    seuil = max(bruit * 3, float(e.max()) * 0.05, 0.003)
    voix = np.flatnonzero(e > seuil)
    if len(voix) == 0:
        return audio, False, False
    premier, dernier = int(voix[0]), int(voix[-1])
    marge = int(MARGE_S * FREQ / TRAME)
    debut, fin = max(0, premier - marge), min(len(e), dernier + 1 + marge)
    return audio[debut * TRAME: fin * TRAME], premier < 3, dernier > len(e) - 4


def controler(audio: np.ndarray, type_: str = "phrase", debut_coupe=False, fin_coupee=False) -> tuple:
    """Renvoie (durée, alertes, blocages). Les blocages empêchent l'enregistrement."""
    dmin, dmax = LIMITES.get(type_, LIMITES["phrase"])
    duree = len(audio) / FREQ
    alertes, blocages = [], []
    if duree < dmin:
        blocages.append(f"Enregistrement trop court ({duree:.1f} s). Relisez le texte en entier.")
    if duree > dmax:
        blocages.append(f"Enregistrement trop long ({duree:.1f} s, maximum {dmax:.0f} s). "
                        "Arrêtez l'enregistrement juste après la fin du texte.")
    if len(audio):
        if float(np.sqrt(np.mean(audio ** 2))) < RMS_MIN:
            blocages.append("Le son est presque inaudible. Vérifiez le micro sélectionné et rapprochez-vous.")
        if float(np.max(np.abs(audio))) >= 0.99:
            alertes.append("Le son sature : parlez un peu plus loin du micro.")
    if debut_coupe:
        alertes.append("Le début semble coupé : lancez l'enregistrement avant de parler.")
    if fin_coupee:
        alertes.append("La fin semble coupée : attendez un instant avant d'arrêter.")
    return duree, alertes, blocages


def en_wav(audio: np.ndarray) -> bytes:
    tampon = io.BytesIO()
    sf.write(tampon, audio, FREQ, subtype="PCM_16", format="WAV")
    return tampon.getvalue()
