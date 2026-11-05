import json
import sys
from pathlib import Path

import numpy as np

from collecte import audio as A
from collecte import jeux as J
from collecte.stockage import StockageLocal, ecrire_json, lire_json

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import telecharger_donnees  # noqa: E402
import valider_enregistrements as V  # noqa: E402


def preparer(tmp_path):
    s = StockageLocal(tmp_path / "stock")
    wav = A.en_wav(np.zeros(A.FREQ, dtype="float32"))
    journal = []
    for i, texte in enumerate(["pas d'épanchement droit point", "nodule de 12 millimètres point"]):
        f = f"brut/dr01_p{i}.wav"
        s.ecrire(f, wav)
        journal.append({"code": "dr01", "id_phrase": f"p{i}", "type": "phrase", "fichier": f,
                        "duree_s": 1.0, "texte_final": texte})
    ecrire_json(s, "journaux/dr01.json", journal)
    return s, journal


def test_comparaison_signale_les_erreurs_critiques():
    assert V.comparer("pas d'épanchement droit point", "Pas d'épanchement droit.", 0.25)["statut"] == "ok"
    r = V.comparer("pas d'épanchement droit point", "épanchement gauche.", 0.25)
    assert "négation" in r["raisons"] and "latéralité" in r["raisons"]
    assert "nombres" in V.comparer("nodule de 12 millimètres", "nodule de 2 millimètres", 0.9)["raisons"]


def test_validation_incrementale(tmp_path):
    s, _ = preparer(tmp_path)
    faux = lambda chemins: ["pas d'épanchement droit.", "nodule de 2 millimètres."][:len(chemins)]
    res = V.valider(s, faux, "faux-modele", 0.25)
    assert res["brut/dr01_p0.wav"]["statut"] == "ok"
    assert res["brut/dr01_p1.wav"]["statut"] == "a_verifier"
    appels = []
    V.valider(s, lambda c: appels.append(c) or [], "faux-modele", 0.25)
    assert appels == []          # rien à revalider


def test_telechargement_et_fractions_emboitees(tmp_path):
    s, journal = preparer(tmp_path)
    journal = [dict(j, id_phrase=f"q{k:03d}", fichier=journal[0]["fichier"]) for k in range(100) for j in journal[:1]]
    r = J.creer_instantane(s, journal, set(), "abc", False)
    sortie = telecharger_donnees.telecharger(s, tmp_path / "dl", None, [0.25, 0.5])
    lire = lambda n: [json.loads(l) for l in (sortie / n).read_text().splitlines()]
    t25, t50, t = lire("train_25.json"), lire("train_50.json"), lire("train.json")
    assert len(t25) < len(t50) < len(t) or len(t) == 0
    assert all(l in t50 for l in t25)
    assert all(Path(l["audio_filepath"]).is_absolute() for l in t)
