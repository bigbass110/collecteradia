"""Écritures concurrentes de validations/resultats.json (étiquetage GPU, validation, application)."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from collecte import audio as A
from collecte import importation as I
from collecte import jeux as J
from collecte import travail as TR
from collecte.stockage import StockageLocal, ecrire_json, lire_json

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import valider_enregistrements as V  # noqa: E402

CR = "Pas d'adénopathie axillaire. Classé BI-RADS 4."
DICTEE = "pas d'adénopathie axillaire point classé BI-RADS 4 point"


def mots(texte):
    return [{"mot": m, "debut": 0.4 * k, "fin": 0.4 * k + 0.3} for k, m in enumerate(texte.split())]


def lecture(s, code="dr01"):
    f = f"brut/{code}_p0.wav"
    s.ecrire(f, A.en_wav(np.zeros(A.FREQ, dtype="float32")))
    ecrire_json(s, f"journaux/{code}.json", [{"code": code, "id_phrase": "p0", "type": "phrase", "fichier": f,
                                             "duree_s": 1.0, "texte_final": "absence de pneumothorax point"}])
    return f


def test_maj_resultats_ne_touche_que_ses_lignes(tmp_path):
    s = StockageLocal(tmp_path)
    J.maj_resultats(s, {"a.wav": {"statut": "ok"}, "b.wav": {"statut": "ok"}})
    J.maj_resultats(s, {"c.wav": {"statut": "a_verifier"}}, retires=["a.wav"])
    assert lire_json(s, J.CLE_RESULTATS, {}) == {"b.wav": {"statut": "ok"}, "c.wav": {"statut": "a_verifier"}}


def test_etiquetage_gpu_n_efface_plus_une_validation_simultanee(tmp_path):
    """Pendant l'étiquetage d'une dictée, un autre programme écrit un résultat : il doit survivre."""
    s = StockageLocal(tmp_path)
    I.enregistrer(s, "dr02", A.en_wav(np.full(A.FREQ * 8, 0.01, dtype="float32")), 8.0, CR, "", "radiologue")

    def parakeet(chemin, modalite):
        J.maj_resultats(s, {"brut/dr01_p0.wav": {"statut": "ok", "hypothese": "écrit pendant l'étiquetage"}})
        return mots(DICTEE)

    TR.traiter(s, parakeet, lambda c, m: mots(DICTEE), set())
    resultats = lire_json(s, J.CLE_RESULTATS, {})
    assert resultats["brut/dr01_p0.wav"]["hypothese"] == "écrit pendant l'étiquetage"
    assert any(f.startswith("brut/hist_") for f in resultats)


def test_validation_n_efface_plus_un_etiquetage_simultane(tmp_path):
    s = StockageLocal(tmp_path)
    lecture(s)

    def transcrire(chemins):
        J.maj_resultats(s, {"brut/hist_h1_s001.wav": {"statut": "ok"}})       # écrit par le GPU entre-temps
        return ["Absence de pneumothorax."] * len(chemins)

    V.valider(s, transcrire, "parakeet", 0.25)
    resultats = lire_json(s, J.CLE_RESULTATS, {})
    assert set(resultats) == {"brut/dr01_p0.wav", "brut/hist_h1_s001.wav"}
    assert not J.validation_en_cours(s)                                      # marqueur retiré à la fin


def test_validation_refusee_pendant_un_etiquetage(tmp_path):
    s = StockageLocal(tmp_path)
    lecture(s)
    ecrire_json(s, TR.CLE_TRAVAIL, {"statut": "en_cours", "maj": TR.maintenant()})
    with pytest.raises(SystemExit, match="étiquetage GPU est en cours"):
        V.valider(s, lambda c: [], "parakeet", 0.25)


def test_marqueur_retire_meme_si_la_validation_plante(tmp_path):
    s = StockageLocal(tmp_path)
    lecture(s)

    def panne(chemins):
        assert J.validation_en_cours(s)                                      # marqueur posé pendant le travail
        raise RuntimeError("GPU indisponible")

    with pytest.raises(RuntimeError):
        V.valider(s, panne, "parakeet", 0.25)
    assert not J.validation_en_cours(s)


def test_etiquetage_refuse_pendant_une_validation_et_libere_son_etat(tmp_path):
    s = StockageLocal(tmp_path)
    J.marquer_validation(s, True)
    with pytest.raises(RuntimeError, match="validation"):
        TR.traiter(s, lambda c, m: [], lambda c, m: [], set(), id_travail="t1")
    assert lire_json(s, TR.CLE_TRAVAIL, {})["statut"] == "refuse"
    assert not TR.travail_en_cours(s)                                        # le bouton redevient utilisable
    with pytest.raises(SystemExit, match="Une autre validation"):
        V.valider(s, lambda c: [], "parakeet", 0.25)


def test_marqueur_perime_ignore(tmp_path):
    s = StockageLocal(tmp_path)
    vieux = (datetime.now(timezone.utc) - timedelta(minutes=25)).isoformat(timespec="seconds")
    ecrire_json(s, J.CLE_VALIDATION_EN_COURS, {"maj": vieux})
    assert not J.validation_en_cours(s)                                      # programme interrompu il y a longtemps
