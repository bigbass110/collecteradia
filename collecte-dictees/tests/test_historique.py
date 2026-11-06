import io
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import soundfile as sf

from collecte import audio as A
from collecte import etiquetage as E
from collecte import importation as I
from collecte import jeux as J
from collecte import texte as T
from collecte import travail as TR
from collecte.stockage import StockageLocal, lire_json

CR = ("Nom : DIOP Amadou\nMammographie bilatérale\nSeins de densité B. Pas de microcalcification.\n"
      "Nodule spiculé de 12 x 8 mm du sein gauche.\nPas d'adénopathie axillaire.\nConclusion : classé BI-RADS 4.")
DICTEE_P = ("monsieur Diop euh nodule spécule de 12 x 8 mm du sein gauche point à la ligne "
            "pas d'adénopathie axillaire point classé BI-RADS 4 point")
DICTEE_W = DICTEE_P.replace(" euh", "").replace("spécule", "spiculé")


def mots(texte, pauses=None, pas=0.35):
    pauses, sortie, t = pauses or {}, [], 0.0
    for k, m in enumerate(texte.split()):
        t += pauses.get(k, 0)
        sortie.append({"mot": m, "debut": round(t, 2), "fin": round(t + 0.3, 2)})
        t += pas
    return sortie


def test_etiquetage_corrige_ignore_le_modele_et_exclut_les_noms():
    r = E.etiqueter(mots(DICTEE_P, {3: 1.0, 17: 0.8}), mots(DICTEE_W, {2: 1.0, 16: 0.8}), CR)
    textes = [s["texte"] for s in r["segments"]]
    assert textes == ["nodule spiculé de 12 fois 8 millimètres du sein gauche point à la ligne",
                      "pas d'adénopathie axillaire point classé BI-RADS 4 point"]
    assert all(s["statut"] == "ok" for s in r["segments"])
    assert r["exclus_identifiants"] == 1
    assert not any("diop" in t.lower() or "microcalcification" in t for t in textes)   # nom exclu, modèle ignoré


def test_correction_non_confirmee_par_whisper_est_a_verifier():
    r = E.etiqueter(mots(DICTEE_P, {3: 1.0}), mots(DICTEE_P.replace(" euh", ""), {2: 1.0}), CR)
    seg = r["segments"][0]
    assert seg["statut"] == "a_verifier" and any("non confirmée" in x for x in seg["raisons"])


def test_compte_rendu_mal_apparie_est_signale():
    r = E.etiqueter(mots(DICTEE_P), mots(DICTEE_W), "IRM du genou droit. Ménisque normal. Ligaments continus.")
    assert r["concordance"] < E.CONCORDANCE_MIN
    assert all(s["statut"] == "a_verifier" for s in r["segments"])


def test_appariement_et_extraction():
    f = lambda n: SimpleNamespace(name=n)
    paires, pbs = I.apparier([f("0001.M4A"), f("0001.docx"), f("0002.mp3"), f("0003.txt"), f("x.exe")])
    assert [(a.name, t.name) for a, t in paires] == [("0001.M4A", "0001.docx")]
    assert len(pbs) == 3
    assert I.extraire_texte("a.txt", "Épanchement\r\n\r\npleural".encode("cp1252")) == "Épanchement\npleural"
    from docx import Document
    tampon = io.BytesIO()
    doc = Document(); doc.add_paragraph("Nodule  spiculé"); doc.save(tampon)
    assert I.extraire_texte("a.docx", tampon.getvalue()) == "Nodule spiculé"


def test_import_refuse_doublon_et_vide(tmp_path):
    s = StockageLocal(tmp_path)
    wav = A.en_wav(np.zeros(A.FREQ * 10, dtype="float32"))
    ident, motif = I.enregistrer(s, "dr01", wav, 10.0, "texte", "", "radiologue")
    assert ident and motif is None
    assert I.enregistrer(s, "dr01", wav, 10.0, "texte", "", "radiologue") == (None, "déjà importé")
    assert I.enregistrer(s, "dr01", b"autre", 10.0, "  ", "", "radiologue")[0] is None
    assert "nom" not in json.dumps(lire_json(s, "historique/index/dr01.json", []))


def test_traitement_complet_et_instantane(tmp_path):
    s = StockageLocal(tmp_path)
    duree = 12.0
    audio = (0.1 * np.sin(np.linspace(0, 2000, int(duree * A.FREQ)))).astype("float32")
    ids = []
    for _ in range(12):
        bruit = audio + np.random.default_rng(len(ids)).normal(0, 1e-4, len(audio)).astype("float32")
        ident, _ = I.enregistrer(s, "dr01", A.en_wav(bruit), duree, CR, "Sénologie", "radiologue")
        ids.append(ident)
    fp = lambda chemin, mod: mots(DICTEE_P, {3: 1.0, 17: 0.8})
    fw = lambda chemin, mod: mots(DICTEE_W, {2: 1.0, 16: 0.8})
    bilan = TR.traiter(s, fp, fw, set())
    assert bilan["statut"] == "termine" and bilan["traitees"] == 12 and bilan["erreurs"] == 0
    journal = lire_json(s, "journaux/hist_dr01.json", [])
    assert len(journal) == 24
    resultats = lire_json(s, J.CLE_RESULTATS, {})
    reserves = [d for d in ids if T.ensemble_de(d) == "test"]
    for j in journal:
        attendu = "a_verifier" if j["groupe"] in reserves else "ok"
        assert resultats[j["fichier"]]["statut"] == attendu
        assert s.lire(j["fichier"]) is not None
    assert TR.traiter(s, fp, fw, set())["total"] == 0          # rien à refaire

    r = J.creer_instantane(s, journal, set(), "abc", False)
    for nom in ("train", "val", "test_historique"):
        lignes = [json.loads(l) for l in s.lire(f"manifestes/{r['instantane']}/{nom}.json").decode().splitlines()]
        assert all(l["source"] == "historique" for l in lignes)
        if nom == "test_historique":
            assert lignes == []                                  # rien n'est relu : rien en test
    groupes = {}
    for nom in ("train", "val"):
        for l in s.lire(f"manifestes/{r['instantane']}/{nom}.json").decode().splitlines():
            groupes.setdefault(json.loads(l)["id_phrase"].split("_s")[0], set()).add(nom)
    assert all(len(v) == 1 for v in groupes.values())           # une dictée n'est jamais coupée entre deux ensembles


def test_erreur_sur_une_dictee_n_arrete_pas_le_lot(tmp_path):
    s = StockageLocal(tmp_path)
    for k in range(2):
        I.enregistrer(s, "dr02", A.en_wav(np.full(A.FREQ * 6, 0.01 * (k + 1), dtype="float32")), 6.0, CR, "", "radiologue")
    appels = []

    def fp(chemin, mod):
        appels.append(chemin)
        if len(appels) == 1:
            raise RuntimeError("panne simulée")
        return mots(DICTEE_P)
    bilan = TR.traiter(s, fp, lambda c, m: mots(DICTEE_W), set())
    assert bilan["erreurs"] == 1 and bilan["traitees"] == 2
    assert TR.traiter(s, fp, lambda c, m: mots(DICTEE_W), set(), relancer_erreurs=True)["total"] == 1


# ─────────────── Correctifs P0 : indépendance de Whisper, ponctuation du moteur, noms prononcés ───────────────

def test_invite_whisper_sans_vocabulaire_du_lexique():
    lexique = T.lire_lexique((Path(__file__).resolve().parents[1] / "lexique.csv").read_bytes())
    for modalite in ("", "Sénologie", "Thorax"):
        invite = E.invite_whisper(modalite).lower()
        assert invite.startswith("compte rendu radiologique")
        assert not [l["terme"] for l in lexique if l["categorie"] in {"terme_rare", "score", "eponyme",
                                                                      "paire_dangereuse"}
                    and l["terme"].lower() in invite]


def test_ponctuation_du_moteur_rend_le_segment_a_verifier():
    # Le radiologue a dit « virgule » ; Parakeet l'a remplacé par « , » et les deux moteurs concordent.
    p = "pas de microcalcification, pas d'adénopathie axillaire point"
    r = E.etiqueter(mots(p), mots(p), CR)
    assert r["ponctuation_moteur"] == 1
    seg = r["segments"][0]
    assert seg["texte"] == "pas de microcalcification pas d'adénopathie axillaire point"
    assert seg["statut"] == "a_verifier" and any("ponctuation « , »" in x for x in seg["raisons"])


def test_signe_isole_et_signe_suivi_de_la_commande():
    # Signe écrit comme un mot séparé : rattaché au mot précédent, donc signalé.
    r = E.etiqueter(mots("pas de microcalcification , pas d'adénopathie axillaire point"), mots(DICTEE_W), CR)
    assert r["ponctuation_moteur"] == 1
    # Signe ET commande écrits (« gauche. point ») : la commande n'est pas perdue, rien à signaler.
    p = "pas d'adénopathie axillaire. point classé BI-RADS 4 point"
    r = E.etiqueter(mots(p), mots(p), CR)
    assert r["ponctuation_moteur"] == 0
    assert all(s["statut"] == "ok" for s in r["segments"])


def test_virgule_decimale_n_est_pas_de_la_ponctuation():
    j = E._jetons_mots(mots("masse de 3,5 centimètres"))
    assert [x[0] for x in j] == ["masse", "de", "3,5", "centimètres"]
    assert all(x[4] == "" for x in j)
    j = E._jetons_mots(mots("nodule de 12 mm."))
    assert [(x[0], x[4]) for x in j][-2:] == [("12", ""), ("millimètres", ".")]


def test_nom_prononce_absent_du_compte_rendu_est_a_verifier():
    # « Ndiaye » n'est pas dans l'en-tête du compte rendu : _marquer_identifiants ne le connaît pas.
    p = "pas d'adénopathie axillaire vu avec Ndiaye classé BI-RADS 4 point"
    r = E.etiqueter(mots(p), mots(p), CR)
    seg = r["segments"][0]
    assert seg["statut"] == "a_verifier" and any("nom propre possible (« Ndiaye »)" in x for x in seg["raisons"])


def test_majuscules_legitimes_ne_sont_pas_des_noms():
    # Début de phrase après un « . » du moteur, sigle, mot à chiffre, éponyme du lexique : pas suspects.
    j = E._jetons_mots(mots("Kyste rénal Bosniak 2. Contrôle IRM T2 point"))
    suspects = E._noms_suspects(j, set(), frozenset({"bosniak"}))
    assert suspects == set()
    # Début de phrase après une commande « point » dictée.
    j = E._jetons_mots(mots("kyste rénal point Contrôle conseillé"))
    _, fin = E._marquer_commandes([x[0] for x in j])
    assert E._noms_suspects(j, fin, frozenset()) == set()
    # Éponyme absent du lexique : signalé (à ajouter au lexique).
    j = E._jetons_mots(mots("segment de Couinaud"))
    assert E._noms_suspects(j, set(), frozenset()) == {2}


def test_numero_long_prononce_est_exclu():
    p = "dossier numéro 4589123 pas d'adénopathie axillaire point classé BI-RADS 4 point"
    r = E.etiqueter(mots(p, {3: 1.0}), mots(p, {3: 1.0}), CR)
    assert r["exclus_identifiants"] >= 1
    assert not any("4589123" in s["texte"] for s in r["segments"])
