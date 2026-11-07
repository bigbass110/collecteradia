import sys
from pathlib import Path

import pytest

from collecte import generation as G
from collecte import texte as T
from collecte.stockage import StockageLocal, ecrire_json

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import generer_phrases as GP  # noqa: E402

RACINE = Path(__file__).resolve().parents[1]
LEXIQUE = T.lire_lexique((RACINE / "lexique.csv").read_bytes())
NOMS = T.noms_propres_du_lexique(LEXIQUE)
CR = """Nom : DIOP Amadou
Adressé par le Dr Fall
Mammographie bilatérale.
Seins de densité B. Opacité spiculée de 12 x 8 mm du quadrant supéro-externe gauche.
Pas d'adénopathie axillaire, pas de microcalcification suspecte.
Examen comparé au 12/03/2024.
Vu avec Amadou en fin d'examen.
Conclusion : opacité classée BI-RADS 4. Biopsie recommandée."""


@pytest.mark.parametrize("ecrit, attendu", [
    ("Nodule spiculé de 12 x 8 mm du sein gauche.", "nodule spiculé de 12 fois 8 millimètres du sein gauche point"),
    ("Lésion de 3.5 cm, rehaussée.", "lésion de 3,5 centimètres virgule rehaussée point"),
    ("Masse de 1 cm du lobe droit.", "masse de 1 centimètre du lobe droit point"),
    ("Lésion du segment VII (LI-RADS 5).",
     "lésion du segment 7 ouvrez la parenthèse LI-RADS 5 fermez la parenthèse point"),
    ("Kyste rénal Bosniak II de 18 mm.", "kyste rénal Bosniak 2 de 18 millimètres point"),
    ("- Foie de taille normale.", "foie de taille normale point"),
    ("Hernie L4 L5 ; pas de conflit", "hernie L4 L5 point-virgule pas de conflit"),
    ("Conclusion : nodule classé BI-RADS 4.", "conclusion deux points nodule classé BI-RADS 4 point"),
    ("Volume de 12 cc.", "volume de 12 millilitres point"),
])
def test_conversion_selon_la_convention(ecrit, attendu):
    texte, motif = G.convertir(ecrit, NOMS)
    assert (texte, motif) == (attendu, None)
    assert not [m for n, m in T.verifier_texte(texte, "phrase", NOMS) if n == "erreur"]


@pytest.mark.parametrize("ecrit, motif", [
    ("Patiente de 45 ans, douleur.", "en-tête"),
    ("Lésion stable depuis 45 ans environ.", "âge"),
    ("Examen comparé au 12/03/2024.", "date"),
    ("Contrôle prévu le 3 mars prochain.", "date"),
    ("Comparé à l'examen de 2019.", "date"),
    ("Valeur 4589123 relevée sur la console.", "numéro"),
    ("Résultat transmis à Mme la responsable.", "civilité"),
    ("Atteinte de 30 % du parenchyme.", "symbole"),
    ("Densité cf. examen antérieur.", "abréviation ou phrase coupée"),
    ("Pas d'anomalie.", "trop courte"),
])
def test_rejets(ecrit, motif):
    assert G.convertir(ecrit, NOMS) == (None, motif)


def test_majuscule_en_milieu_de_phrase_rejetee_sauf_sigle_et_eponyme():
    assert G.convertir("Lésion vue avec Ndiaye ce matin.", NOMS)[1].startswith("majuscule")
    assert G.convertir("Nodule suivi selon Fleischner à 12 mois.", NOMS)[0] == \
        "nodule suivi selon Fleischner à 12 mois point"


def test_compte_rendu_complet_sans_aucune_donnee_identifiante():
    phrases, rejets = G.phrases_du_compte_rendu(CR, NOMS)
    assert phrases == [
        "seins de densité B point",
        "opacité spiculée de 12 fois 8 millimètres du quadrant supéro-externe gauche point",
        "pas d'adénopathie axillaire virgule pas de microcalcification suspecte point",
        "conclusion deux points opacité classée BI-RADS 4 point",
    ]
    assert rejets["en-tête"] == 2 and rejets["date"] == 1
    assert rejets["nom de l'en-tête"] == 1                       # « Amadou », trouvé dans l'en-tête
    tout = " ".join(phrases).lower()
    assert "diop" not in tout and "amadou" not in tout and "fall" not in tout


def test_identifiant_stable_et_selection():
    assert G.identifiant("Foie normal point") == G.identifiant("foie  normal point")
    candidats = {
        "opacité spiculée du sein gauche point": {"modalite": "Sénologie", "comptes_rendus": 1},
        "masse spiculée rétro-aréolaire droite point": {"modalite": "Sénologie", "comptes_rendus": 1},
        "kyste anéchogène du sein droit point": {"modalite": "Sénologie", "comptes_rendus": 3},
        "foie de taille normale point": {"modalite": "Abdomen", "comptes_rendus": 9},
        "absence de pneumothorax point": {"modalite": "Thorax", "comptes_rendus": 9},   # déjà en banque
    }
    existants = [{"id": "tho007", "texte": "absence de pneumothorax point"}]
    choisies = G.selectionner(candidats, LEXIQUE, existants, maximum=10, max_par_terme=1,
                              poids={"anéchogène": 5.0})
    textes = [c["texte"] for c in choisies]
    assert textes[0] == "kyste anéchogène du sein droit point"          # terme le plus pondéré d'abord
    assert sum("spiculée" in t for t in textes[:2]) == 1               # un seul par terme (max_par_terme=1)
    assert "absence de pneumothorax point" not in textes               # déjà dans la banque
    assert textes[-1] == "foie de taille normale point"                # sans terme : complète à la fin
    assert len(G.selectionner(candidats, LEXIQUE, existants, maximum=2)) == 2
    assert all("gauche" not in c["termes"] and "droit" not in c["termes"] for c in choisies)   # non ciblés


def test_script_proposer_puis_integrer(tmp_path):
    s = StockageLocal(tmp_path / "stock")
    ecrire_json(s, "historique/index/dr01.json", [{"id": "h1", "code": "dr01", "modalite": "Sénologie"}])
    s.ecrire("historique/cr/h1.txt", CR.encode("utf-8"))
    banque = tmp_path / "phrases.csv"
    banque.write_text("id,type,modalite,texte\nsen005,phrase,Sénologie,absence d'adénopathie axillaire point",
                      encoding="utf-8")                                   # sans saut de ligne final

    choisies, rejets, n = GP.proposer(GP.comptes_rendus_importes(s), LEXIQUE, T.lire_banque(banque.read_bytes()))
    assert n == 4 and len(choisies) == 4 and all(c["modalite"] == "Sénologie" for c in choisies)
    propositions = tmp_path / "phrases_proposees.csv"
    GP.ecrire_propositions(propositions, choisies)

    # Relecture : le radiologue supprime une ligne et en abîme une autre.
    entete, *lignes = propositions.read_text(encoding="utf-8").splitlines()
    abimee = next(l for l in lignes if "virgule" in l).replace(" virgule", ",")
    gardee = next(l for l in lignes if "virgule" not in l)
    lignes = [entete, gardee, abimee, gardee]                     # les autres lignes sont supprimées
    propositions.write_text("\n".join(lignes) + "\n", encoding="utf-8")
    ajoutees, refus = GP.integrer(propositions, banque, LEXIQUE)
    assert len(ajoutees) == 1
    assert refus[0][1].startswith("colonne en trop")              # virgule ajoutée sans guillemets
    assert refus[1][1] == "identifiant déjà utilisé"
    contenu = banque.read_text(encoding="utf-8").splitlines()
    assert contenu[1] == "sen005,phrase,Sénologie,absence d'adénopathie axillaire point"
    assert contenu[2].startswith(ajoutees[0]["id"] + ",phrase,Sénologie,")
    assert not [p for p in T.verifier_banque(T.lire_banque(banque.read_bytes()), []) if p[0] == "erreur"]


def test_integrer_refuse_une_ligne_hors_convention(tmp_path):
    banque = tmp_path / "phrases.csv"
    banque.write_text("id,type,modalite,texte\n", encoding="utf-8")
    propositions = tmp_path / "p.csv"
    propositions.write_text('id,type,modalite,texte\ng1,phrase,Thorax,"lésion de 12 mm, rehaussée point"\n'
                            "g2,phrase,Thorax,absence de pneumothorax point\n", encoding="utf-8")
    ajoutees, refus = GP.integrer(propositions, banque, LEXIQUE)
    assert [a["id"] for a in ajoutees] == ["g2"] and refus[0][0] == "g1"


def test_dossier_de_comptes_rendus(tmp_path):
    (tmp_path / "a.txt").write_text(CR, encoding="utf-8")
    (tmp_path / "b.exe").write_bytes(b"x")
    cr = GP.comptes_rendus_du_dossier(tmp_path, "Sénologie")
    assert len(cr) == 1 and cr[0][1] == "Sénologie"
