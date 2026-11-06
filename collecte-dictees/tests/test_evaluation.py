import json
import sys
from pathlib import Path

from collecte import evaluation as EV
from collecte import texte as T

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import evaluer  # noqa: E402

LEXIQUE = T.lire_lexique((Path(__file__).resolve().parents[1] / "lexique.csv").read_bytes())
REF = "nodule spiculé de 12 fois 8 millimètres du sein gauche point classé BI-RADS 4 point"


def test_sortie_du_modele_de_base_ramenee_a_la_convention():
    # Parakeet de base : majuscules, « x », « mm », signes au lieu des commandes.
    r = EV.comparer(REF, "Nodule spiculé de 12 x 8 mm du sein gauche. Classé BI-RADS 4.", LEXIQUE)
    assert r["erreurs_hc"] == 0 and r["mots_hc"] == 13          # aucune erreur hors commandes
    assert r["erreurs"] == 2 and r["cmd_ref"] == 2 and r["cmd_ok"] == 0
    assert not any(r["critiques"].values())
    assert r["termes"] == {"BI-RADS": True, "millimètres": True}   # « spiculé » ≠ « spiculée » du lexique


def test_commande_longue_comptee_une_seule_fois():
    r = EV.comparer("pas d'épanchement point à la ligne", "pas d'épanchement point", LEXIQUE)
    assert r["cmd_ref"] == 1 and r["cmd_hyp"] == 1 and r["cmd_ok"] == 0
    assert r["erreurs_hc"] == 0


def test_erreurs_critiques():
    r = EV.comparer("pas d'épanchement pleural droit", "épanchement pleural gauche", LEXIQUE)
    assert r["critiques"] == {"negation": True, "lateralite": True}
    r = EV.comparer("ni à droite ni à gauche", "ni à droite à gauche", LEXIQUE)
    assert r["critiques"]["negation"] is True                    # compté, pas seulement présent
    r = EV.comparer("kyste de 3,5 centimètres", "kyste de 3,5 cm.", LEXIQUE)
    assert r["critiques"] == {"nombres": False}
    assert EV.comparer("BI-RADS 4", "BI-RADS 3", LEXIQUE)["critiques"] == {"nombres": True}


def test_rappel_des_termes_par_categorie():
    res = [EV.comparer("plage de verre dépoli", "plage de verre dépoli.", LEXIQUE),
           EV.comparer("kyste anéchogène", "kyste à néchogène", LEXIQUE),
           EV.comparer("nodule hypoéchogène", "nodule hyperéchogène", LEXIQUE)]
    a = EV.agreger(res, LEXIQUE)
    assert a["termes"]["terme_rare"] == {"retrouves": 1, "attendus": 2, "rappel": 0.5}
    assert a["termes"]["paire_dangereuse"]["rappel"] == 0.0
    assert a["par_terme"]["anéchogène"] == {"retrouves": 0, "attendus": 1}


def test_intervalle_par_groupes_et_ecart_apparie():
    refs = [f"nodule du sein gauche numéro {k}" for k in range(40)]
    groupes = [f"v:dr{k % 8}" for k in range(40)]
    bon = [EV.comparer(r, r, LEXIQUE) for r in refs]
    mauvais = [EV.comparer(r, r.replace("sein", "seins") if k % 2 else r, LEXIQUE) for k, r in enumerate(refs)]
    ic = EV.intervalle(mauvais, groupes)
    wer = EV.agreger(mauvais, LEXIQUE)["wer_hors_commandes"]
    assert ic[0] <= wer <= ic[1]
    assert ic == EV.intervalle(mauvais, groupes)                 # déterministe
    ecart = EV.intervalle(bon, groupes, ecart_avec=mauvais)
    assert ecart[1] < 0                                          # le bon modèle est réellement meilleur
    assert EV.intervalle(bon, ["v:seul"] * 40) is None           # une seule voix : pas d'intervalle


def test_groupe_dictee_reelle_ou_voix():
    assert EV.groupe_de({"type": "dictee_reelle", "id_phrase": "h1a2b3c_s004", "code": "dr01"}) == "d:h1a2b3c"
    assert EV.groupe_de({"type": "phrase", "id_phrase": "sen001", "code": "dr01"}) == "v:dr01"


def test_script_complet_sans_gpu(tmp_path, capsys):
    lignes = [{"audio_filepath": f"/a/{k}.wav", "duration": 2.0 if k else 45.0, "text": REF,
               "code": f"dr{k % 3}", "id_phrase": f"p{k}", "type": "phrase"} for k in range(6)]
    (tmp_path / "test_voix_connues.json").write_text("\n".join(json.dumps(l) for l in lignes))
    (tmp_path / "test_historique.json").write_text("")
    manifestes = evaluer.lire_manifestes(tmp_path, evaluer.ENSEMBLES_TEST)
    assert list(manifestes) == ["test_voix_connues"]             # jeux vides ignorés

    appels = []

    def base(chemins, lot):
        appels.append((len(chemins), lot))
        return ["Nodule spiculé de 12 x 8 mm du sein droit. Classé BI-RADS 4."] * len(chemins)
    hyp = {"base": evaluer.transcrire_tout(manifestes, base, 4),
           "affine": {l["audio_filepath"]: REF for l in lignes}}
    assert sorted(appels) == [(1, 1), (1, 4), (4, 4)]            # l'audio long est transcrit seul

    rapport = evaluer.evaluer(manifestes, hyp, LEXIQUE)
    m = rapport["ensembles"]["test_voix_connues"]["mesures"]
    assert m["base"]["critiques"]["lateralite"]["taux"] == 1.0
    assert m["affine"]["wer"] == 0 and m["affine"]["commandes_rappel"] == 1.0
    assert m["affine"]["ecart_wer_hors_commandes"] < 0
    assert rapport["ensembles"]["test_voix_connues"]["detail"][0]["critiques"]["base"] == ["lateralite"]
    texte = evaluer.tableau(rapport)
    assert "jeu petit" in texte and "Latéralité fausse" in texte and "écart avec la référence" in texte
    json.dumps(rapport)                                          # sérialisable
