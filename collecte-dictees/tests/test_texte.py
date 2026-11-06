from pathlib import Path

import pytest

from collecte import texte as T

RACINE = Path(__file__).resolve().parents[1]


def test_la_banque_livree_ne_contient_aucune_erreur():
    phrases = T.lire_banque((RACINE / "phrases.csv").read_bytes())
    lexique = T.lire_lexique((RACINE / "lexique.csv").read_bytes())
    assert [p for p in T.verifier_banque(phrases, lexique) if p[0] == "erreur"] == []


@pytest.mark.parametrize("texte, motif", [
    ("nodule de 12 x 8 millimètres", "« x »"),
    ("nodule de 12 mm", "unité abrégée"),
    ("lésion de 3.5 centimètres", "décimale"),
    ("segment VII", "romains"),
    ("douze millimètres", "lettres"),
    ("examen du 12 mars", "date"),
    ("adressé par le Dr Diop", "civilité"),
    ("taille normale, sans lésion", "virgule hors"),
    ("pas d'épanchement.", "ponctuation"),
])
def test_le_correcteur_refuse(texte, motif):
    assert any(motif in m for n, m in T.verifier_texte(texte) if n == "erreur")


@pytest.mark.parametrize("texte", [
    "kyste de 2,5 centimètres point",
    "nodule de 12 fois 8 millimètres classé BI-RADS 4 point à la ligne",
    "discopathie L4 L5 avec hernie droite point",
    "pas d'hypersignal T2 FLAIR virgule ligne médiane en place point",
])
def test_le_correcteur_accepte(texte):
    assert [m for n, m in T.verifier_texte(texte) if n == "erreur"] == []


def test_termes_avec_apostrophe_et_frontieres_de_mots():
    lex = T.lire_lexique("terme,categorie\npas d',negation\ndroit,lateralite\n".encode())
    assert T.termes_de("pas d'épanchement droit", lex) == {"pas d'", "droit"}
    assert T.termes_de("épanchement à droite", lex) == set()      # « droit » n'est pas « droite »


def test_repartition_deterministe_et_stable():
    assert T.ensemble_de("sen001") == T.ensemble_de("sen001")
    repart = [T.ensemble_de(f"id{i}") for i in range(2000)]
    assert 0.05 < repart.count("test") / 2000 < 0.15


def test_choix_privilegie_les_termes_peu_couverts():
    restantes = [{"id": "a"}, {"id": "b"}]
    termes = {"a": ["BI-RADS"], "b": ["Bosniak"]}
    choix = T.choisir_phrase(restantes, {"BI-RADS": 5, "Bosniak": 0}, termes, cible=5, graine="x")
    assert choix["id"] == "b"


def test_sans_commandes_et_surlignage():
    assert T.sans_commandes(T.normaliser("foie normal virgule rate normale point à la ligne")) == "foie normal rate normale"
    assert "<span class='cmd'>point à la ligne</span>" in T.surligner_commandes("foie normal point à la ligne")


def test_ids_en_erreur_ne_retient_que_les_erreurs():
    phrases = [{"id": "ok1", "type": "phrase", "modalite": "Thorax", "texte": "absence de pneumothorax point"},
               {"id": "ko1", "type": "phrase", "modalite": "Thorax", "texte": "nodule de 12 mm."},
               {"id": "av1", "type": "phrase", "modalite": "Thorax", "texte": "signe de Couinaud point"}]
    problemes = T.verifier_banque(phrases, [])
    assert T.ids_en_erreur(problemes) == {"ko1"}          # « av1 » n'a qu'un avertissement (majuscule)
