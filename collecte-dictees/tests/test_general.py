import json
import sys
from pathlib import Path

import numpy as np
import pytest

from collecte import audio as A
from collecte import general as G

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import preparer_general as PG  # noqa: E402


@pytest.mark.parametrize("ecrit, attendu", [
    ("Le chat dort sur le canapé.", "le chat dort sur le canapé"),
    ("« Viens ici », dit-elle ; puis elle partit !", "viens ici dit-elle puis elle partit"),
    ("Aujourd’hui, l’équipe a gagné.", "aujourd'hui l'équipe a gagné"),
    ("Il y a une solution.", "il y a une solution"),               # « une » : article, pas un nombre
    ("Paris est la capitale de la France.", "paris est la capitale de la france"),
])
def test_conversion(ecrit, attendu):
    assert G.convertir(ecrit) == (attendu, None)


@pytest.mark.parametrize("ecrit, motif", [
    ("Il est né en 1995.", "chiffres"),
    ("Elle a vingt-deux ans.", "nombre en lettres"),
    ("Trois enfants jouent dehors.", "nombre en lettres"),
    ("Le prix est de 5 %.", "chiffres"),
    ("Il travaille à la SNCF depuis longtemps.", "sigle"),
    ("M. Durand est arrivé.", "abréviation"),
    ("Pommes, poires, etc.", "abréviation"),
    ("Le taux atteint cinquante pour cent.", "nombre en lettres"),
    ("Oui.", "longueur"),
    ("Le ratio est a/b.", "symbole"),
])
def test_rejets(ecrit, motif):
    assert G.convertir(ecrit) == (None, motif)


def enonces(n_voix, par_voix):
    return [{"id": f"v{v}_{k}", "voix": f"v{v}", "texte": "x"} for v in range(n_voix) for k in range(par_voix)]


def test_repartition_voix_disjointes_et_plafond():
    test, reserve = G.repartir(enonces(40, 80), n_test=300, max_par_voix=50)
    assert len(test) == 300
    assert not {e["voix"] for e in test} & {e["voix"] for e in reserve}      # aucune voix de test en train
    assert len({e["voix"] for e in test}) >= 10                               # au plus 30 énoncés par voix
    assert max(sum(e["voix"] == v for e in reserve) for v in {e["voix"] for e in reserve}) <= 50
    assert G.repartir(enonces(40, 80))[1] == reserve                          # déterministe


def test_entrelacement_maximise_les_voix():
    ordre = G.entrelacer(enonces(5, 4))
    assert len({e["voix"] for e in ordre[:5]}) == 5 and len(ordre) == 20


def test_proportion_et_melanges_emboites():
    assert G.secondes_a_ajouter(15 * 3600, 0.25) == pytest.approx(5 * 3600)
    with pytest.raises(ValueError):
        G.secondes_a_ajouter(10, 1.0)
    radio = [{"duration": 10.0}] * 30                                         # 300 s
    general = [{"duration": 5.0, "k": k} for k in range(100)]
    grand = G.melanger(radio, general, 0.25)                                  # 100 s de général
    petit = G.melanger(radio[:15], general, 0.25)                             # 50 s
    assert sum(l["duration"] for l in grand[30:]) == 100.0
    assert petit[15:] == grand[30:30 + len(petit) - 15]                       # emboîtés


def test_script_complet_common_voice(tmp_path):
    cv = tmp_path / "cv"
    (cv / "clips").mkdir(parents=True)
    lignes = ["client_id\tpath\tsentence\tup_votes"]
    phrases = ["le chat dort sur le canapé", "Il est né en 1995.", "la pluie tombe depuis le matin",
               "elle lit un livre au jardin"]
    for v in range(30):
        for k in range(4):
            nom = f"c{v}_{k}.wav"
            duree = 25.0 if (v, k) == (29, 3) else 2.0                        # un clip trop long
            son = (0.1 * np.sin(np.linspace(0, 900, int(duree * A.FREQ)))).astype("float32")
            (cv / "clips" / nom).write_bytes(A.en_wav(son))
            lignes.append(f"voix{v}\t{nom}\t{phrases[k]}\t2")
    (cv / "train.tsv").write_text("\n".join(lignes) + "\n", encoding="utf-8")

    inst = tmp_path / "inst"
    inst.mkdir()
    radio = [{"audio_filepath": f"/r/{k}.wav", "duration": 10.0, "text": "absence de pneumothorax point",
              "code": "dr01", "id_phrase": f"p{k}", "type": "phrase"} for k in range(12)]   # 120 s
    PG.ecrire_jsonl(inst / "train.json", radio)
    PG.ecrire_jsonl(inst / "train_50.json", radio[:6])

    b = PG.preparer(inst, PG.lire_cv(cv), proportion=0.25, n_test=20, max_par_voix=50)
    assert b["rejets"] == {"chiffres": 30}
    assert b["general_test"] == 20
    test = PG.lire_jsonl(inst / "general_test.json")
    general = PG.lire_jsonl(inst / "general_train.json")
    assert not {l["code"] for l in test} & {l["code"] for l in general}
    assert sum(l["duration"] for l in general) >= 40.0                         # 120 × 0,25 / 0,75
    assert all(l["duration"] <= 20 and Path(l["audio_filepath"]).exists() for l in general + test)
    mixte = PG.lire_jsonl(inst / "train_mixte.json")
    assert mixte[:12] == radio and {l["type"] for l in mixte[12:]} == {"general"}
    assert set(b["mixtes"]) == {"train_mixte", "train_50_mixte"}
    assert (inst / "general_resume.json").exists()

    # Relance : l'audio déjà converti est réutilisé, le résultat est identique.
    assert PG.preparer(inst, PG.lire_cv(cv), proportion=0.25, n_test=20)["general_train"] == b["general_train"]
    assert PG.lire_jsonl(inst / "general_train.json") == general


def test_lecture_mls_et_manifeste(tmp_path):
    (tmp_path / "audio" / "10065" / "10039").mkdir(parents=True)
    (tmp_path / "audio" / "10065" / "10039" / "10065_10039_000000.opus").write_bytes(b"")
    (tmp_path / "transcripts.txt").write_text("10065_10039_000000\tle vent souffle fort\n", encoding="utf-8")
    e = PG.lire_mls(tmp_path)[0]
    assert e["voix"] == "10065" and e["audio"].endswith(".opus")
    m = tmp_path / "corpus.json"
    m.write_text(json.dumps({"audio_filepath": "/a.wav", "text": "bonjour à tous", "speaker": 7}) + "\n")
    assert PG.lire_manifeste(m)[0]["voix"] == "7"
