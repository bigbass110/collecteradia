import csv
import sys
from pathlib import Path

from collecte import erreurs as ER
from collecte import jeux as J
from collecte import texte as T
from collecte.stockage import StockageLocal, ecrire_json

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import construire_lexique as CL  # noqa: E402

LEXIQUE = T.lire_lexique("terme,categorie\nspiculée,terme_rare\nverre dépoli,terme_rare\nvirgule,commande\n".encode())


def lecture(k, code, texte, hyp, statut="ok", modele="nvidia/parakeet-tdt-0.6b-v3"):
    f = f"brut/{code}_{k}.wav"
    j = {"code": code, "id_phrase": f"p{k}", "type": "phrase", "fichier": f, "texte_final": texte}
    return j, f, {"statut": statut, "hypothese": hyp, "modele": modele}


def test_mots_manques_comptes_sans_mots_outils_ni_nombres_ni_commandes():
    p = [("dr01", "kyste anéchogène de 8 millimètres virgule sein droit point", "Kyste à néchogène de 9 mm, sein droit."),
         ("dr02", "pas d'adénopathie anéchogène point", "Pas d'adénopathie anéchogène."),
         ("dr02", "nodule anéchogène du sein point", "Nodule enéchogène du sein.")]
    s = ER.compter(p)
    assert s["anéchogène"]["occurrences"] == 3 and s["anéchogène"]["erreurs"] == 2
    assert s["anéchogène"]["voix_erreur"] == {"dr01", "dr02"}
    assert s["adénopathie"]["erreurs"] == 0                        # élision « d' » retirée
    assert "de" not in s and "8" not in s and "virgule" not in s and "point" not in s
    assert s["anéchogène"]["ecrit"] == {"à néchogène": 1, "enéchogène": 1}


def test_candidats_categorie_et_lexique():
    p = ([("dr01", "nodule hypoéchogène point", "Nodule hyperéchogène.")] * 3
         + [("dr02", "nodule hyperéchogène point", "Nodule hyperéchogène.")]
         + [("dr03", "opacité spiculée point", "Opacité spéculée.")] * 2
         + [("dr03", "foie normal point", "Foie normal.")] * 5)
    c = {x["terme"]: x for x in ER.candidats(ER.compter(p), LEXIQUE)}
    assert c["hypoéchogène"]["categorie"] == "paire_dangereuse"
    assert c["hypoéchogène"]["confondu_avec"] == "hyperéchogène" and c["hypoéchogène"]["taux"] == 1.0
    assert c["spiculée"]["categorie"] == "terme_rare" and c["spiculée"]["deja_au_lexique"]
    assert "foie" not in c and "hyperéchogène" not in c          # jamais manqués
    assert list(c)[0] == "hypoéchogène"                          # trié par nombre d'erreurs


def test_paire_reconnue_grace_au_lexique_ou_a_la_banque():
    p = [("dr01", "lésion intra-axiale point", "Lésion extra-axiale.")] * 2
    assert ER.candidats(ER.compter(p), [])[0]["categorie"] == "terme_rare"          # « extra-axiale » inconnu
    c = ER.candidats(ER.compter(p), [], textes_connus=["lésion extra-axiale frontale point"])[0]
    assert c["categorie"] == "paire_dangereuse" and c["confondu_avec"] == "extra-axiale"
    lex = T.lire_lexique("terme,categorie\nintra-axiale,score\nextra-axiale,paire_dangereuse\n".encode())
    assert ER.candidats(ER.compter(p), lex)[0]["categorie"] == "score"             # catégorie du lexique gardée


def test_seuils():
    p = [("dr01", "lésion transfixiante point", "Lésion transfixiante.")] * 8 + [
        ("dr01", "lésion transfixiante point", "Lésion transfixe.")] * 2
    assert ER.candidats(ER.compter(p), [], min_erreurs=2, min_taux=0.3) == []        # 20 % < 30 %
    assert [x["terme"] for x in ER.candidats(ER.compter(p), [], 2, 0.2)] == ["transfixiante"]


def test_bilan_du_lexique_actuel_avec_expressions():
    p = [("dr01", "plage de verre dépoli point", "Plage de verre dépoli."),
         ("dr01", "plage de verre dépoli point", "Plage de verdé poli.")]
    b = ER.bilan_lexique(p, LEXIQUE)
    assert b == [{"terme": "verre dépoli", "categorie": "terme_rare", "attendus": 2, "retrouves": 1,
                  "taux_erreur": 0.5}]


def test_paires_ne_garde_que_les_textes_fiables():
    journal, resultats = [], {}
    for k, (statut, modele) in enumerate([("ok", "parakeet"), ("a_verifier", "parakeet"), ("rejete", "parakeet"),
                                          ("ok", "autre-modele")]):
        j, f, r = lecture(k, "dr01", "texte lu point", "Texte lu.", statut, modele)
        journal.append(j)
        resultats[f] = r
    j, f, r = lecture(9, "dr01", "texte relu point", "Texte relu.", "a_verifier")
    journal.append(j)
    resultats[f] = r
    decisions = {f: {"statut": "valide_humain"}}
    journal.append({"code": "dr02", "id_phrase": "h1_s001", "type": "dictee_reelle", "fichier": "brut/hist_h1_s001.wav",
                    "texte_final": "segment corrigé point", "texte_parakeet": "segment corriger"})
    resultats["brut/hist_h1_s001.wav"] = {"statut": "ok", "hypothese": "Parakeet : … · Whisper : …"}
    p = ER.paires(journal, resultats, decisions)
    assert p == [("dr01", "texte lu point", "Texte lu."), ("dr01", "texte relu point", "Texte relu."),
                 ("dr02", "segment corrigé point", "segment corriger")]


def test_script_complet_et_ajout_au_lexique(tmp_path):
    s = StockageLocal(tmp_path / "stock")
    journal, resultats = [], {}
    textes = [("nodule hypoéchogène point", "Nodule hyperéchogène."), ("nodule hyperéchogène point", "Nodule hyperéchogène."),
              ("opacité spiculée point", "Opacité spéculée."), ("signe de Couinaud point", "Signe de Kouinaud.")]
    for k, (t, h) in enumerate(textes * 2):
        j, f, r = lecture(k, f"dr0{k % 2}", t, h)
        journal.append(j)
        resultats[f] = r
    ecrire_json(s, "journaux/dr00.json", journal)
    ecrire_json(s, J.CLE_RESULTATS, resultats)
    lexique_csv = tmp_path / "lexique.csv"
    lexique_csv.write_text("terme,categorie\nspiculée,terme_rare", encoding="utf-8")   # sans saut de ligne final

    candidats, bilan, n = CL.construire(s, T.lire_lexique(lexique_csv.read_bytes()))
    assert n == 8
    assert {c["terme"] for c in candidats} == {"hypoéchogène", "spiculée", "Couinaud"}
    CL.ecrire_candidats(tmp_path / "c.csv", candidats)
    assert next(csv.DictReader(open(tmp_path / "c.csv", encoding="utf-8")))["terme"] in {"hypoéchogène", "spiculée", "Couinaud"}

    ajoutes = CL.ajouter_au_lexique(lexique_csv, candidats)
    assert ajoutes == ["hypoéchogène"]                            # ni déjà présent, ni majuscule (Couinaud)
    assert lexique_csv.read_text(encoding="utf-8").splitlines() == [
        "terme,categorie", "spiculée,terme_rare", "hypoéchogène,paire_dangereuse"]
    assert bilan[0]["terme"] == "spiculée" and bilan[0]["taux_erreur"] == 1.0
