import json

from collecte import jeux as J
from collecte import texte as T
from collecte.stockage import StockageLocal, ecrire_json


def entree(code, pid, type_="phrase", texte="foie normal point"):
    return {"code": code, "id_phrase": pid, "type": type_, "fichier": f"brut/{code}_{pid}.wav",
            "duree_s": 3.0, "texte_final": texte, "micro": "Micro de dictée", "lieu": "Salle de lecture"}


def test_instantane_respecte_les_regles(tmp_path):
    s = StockageLocal(tmp_path)
    ids = [f"p{i:03d}" for i in range(200)]
    journal = [entree(c, i) for c in ("dr01", "dr02") for i in ids]
    journal += [entree("dr09", i) for i in ids if T.ensemble_de(i) == "test"]   # voix réservée
    journal += [entree("dr01", "cr001", "compte_rendu")]
    ecrire_json(s, J.CLE_RESULTATS, {"brut/dr01_p000.wav": {"statut": "a_verifier"}})
    ecrire_json(s, J.CLE_DECISIONS, {"brut/dr02_p001.wav": {"statut": "rejete"}})

    r = J.creer_instantane(s, journal, {"dr09"}, "abc", exiger_validation=False)
    lire = lambda e: [json.loads(l) for l in s.lire(f"manifestes/{r['instantane']}/{e}.json").decode().splitlines()]
    train, test_c, test_n, cr = lire("train"), lire("test_voix_connues"), lire("test_voix_nouvelles"), lire("test_comptes_rendus")

    assert {l["code"] for l in test_n} == {"dr09"}
    assert "dr09" not in {l["code"] for l in train}
    assert not ({l["id_phrase"] for l in train} & {l["id_phrase"] for l in test_c})   # pas de fuite de texte
    assert [l["id_phrase"] for l in cr] == ["cr001"]
    assert r["exclus"] == {"a_verifier": 1, "rejete": 1}
    assert json.loads(s.lire("manifestes/dernier.json"))["instantane"] == r["instantane"]


def test_exiger_validation_exclut_les_non_valides(tmp_path):
    s = StockageLocal(tmp_path)
    r = J.creer_instantane(s, [entree("dr01", "p001")], set(), "abc", exiger_validation=True)
    assert r["exclus"] == {"non_valide": 1}


def test_couverture_compte_les_voix_distinctes():
    lex = T.lire_lexique("terme,categorie\nBI-RADS,score\n".encode())
    j = [entree("dr01", "a", texte="BI-RADS 4"), entree("dr01", "b", texte="BI-RADS 2"),
         entree("dr02", "a", texte="BI-RADS 4")]
    assert J.couverture(j, lex) == {"BI-RADS": 2}


LEXIQUE = T.lire_lexique("terme,categorie\nspiculée,terme_rare\nBI-RADS,score\ngauche,lateralite\n".encode())


def test_valeur_verification():
    lecture = {"texte_final": "opacité spiculée du sein gauche point", "duree_s": 3.0}
    # Parakeet de base a manqué « spiculée » et s'est trompé de côté.
    pts, motifs = J.valeur_verification(lecture, {"raisons": ["latéralité"], "hypothese": "Opacité spéculée du sein droit."},
                                        LEXIQUE)
    assert pts == 4 + 2 and motifs == ["terme manqué : spiculée", "erreur critique du modèle de base"]
    # Seul un écart global : aucune valeur particulière.
    assert J.valeur_verification(lecture, {"raisons": ["CER > 25%"], "hypothese": "opacité spiculée du sein gauche"},
                                 LEXIQUE) == (0, [])
    # Segment de dictée réelle : la transcription de Parakeet est dans le journal.
    seg = {"texte_final": "nodule classé BI-RADS 4 point", "texte_parakeet": "nodule classé bi rads 4"}
    pts, motifs = J.valeur_verification(seg, {"raisons": ["correction « bi » → « BI-RADS » non confirmée ; "
                                                          "réservé au test : relecture intégrale"]}, LEXIQUE)
    assert pts == 2 + 3 + 2 and "réservé au test" in motifs
    # Compte rendu mal apparié : relégué en fin de liste.
    assert J.valeur_verification(seg | {"texte_parakeet": "nodule classé BI-RADS 4"},
                                 {"raisons": ["compte rendu peut-être mal apparié (concordance 12%)"]}, LEXIQUE)[0] == -3


def test_tri_a_verifier_par_valeur_puis_duree():
    par_fichier = {
        "a.wav": {"texte_final": "foie normal point", "duree_s": 2.0},
        "b.wav": {"texte_final": "opacité spiculée point", "duree_s": 9.0},
        "c.wav": {"texte_final": "foie normal point", "duree_s": 1.0},
        "d.wav": {"texte_final": "foie normal point", "duree_s": 1.0},
    }
    resultats = {
        "a.wav": {"raisons": ["CER > 25%"], "hypothese": "foie normal"},
        "b.wav": {"raisons": ["CER > 25%"], "hypothese": "opacité spéculée"},
        "c.wav": {"raisons": ["CER > 25%"], "hypothese": "foie normal"},
        "d.wav": {"raisons": ["compte rendu peut-être mal apparié"], "hypothese": "foie normal"},
    }
    ordre = [f for f, _, _ in J.trier_a_verifier(list(par_fichier), par_fichier, resultats, LEXIQUE)]
    assert ordre == ["b.wav", "c.wav", "a.wav", "d.wav"]
