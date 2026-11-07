from pathlib import Path

from streamlit.testing.v1 import AppTest

RACINE = Path(__file__).resolve().parents[1]


def aller(at, page: str):
    """Ouvre une page de la navigation (adresse : enregistrer, importer, consignes, administration).
    AppTest.switch_page ne connaît que les pages en fichiers : on lui donne directement l'empreinte de
    l'adresse, calculée comme Streamlit le fait pour les pages en fonctions."""
    try:                                              # Streamlit 1.5x : calc_md5 ; versions récentes : calc_hash
        from streamlit.util import calc_hash
    except ImportError:
        from streamlit.util import calc_md5 as calc_hash
    at._page_hash = calc_hash(page)
    return at.run(timeout=60)


def test_parcours_local(tmp_path, monkeypatch):
    monkeypatch.chdir(RACINE)
    secrets = RACINE / ".streamlit" / "secrets.toml"
    deja = secrets.read_text() if secrets.exists() else None
    secrets.write_text('MODE_ACCES="mot_de_passe"\nSTOCKAGE="local"\nAPP_PASSWORD="x"\n')
    try:
        at = AppTest.from_file(str(RACINE / "app.py")).run(timeout=60)
        at.text_input[0].input("x").run(timeout=60)
        at.sidebar.text_input[0].input("dr97").run(timeout=60)
        assert [s.value for s in at.subheader] == ["Consentement"]
        at.checkbox[0].check().run(timeout=60)
        at.button[0].click().run(timeout=60)
        assert [s.value for s in at.subheader] == ["Avant de commencer"]
        at.button[0].click().run(timeout=60)          # « Commencer la séance »
        assert at.title[0].value == "Lisez le texte"
        assert not at.exception
        aller(at, "importer")
        assert at.title[0].value == "Importer des dictées"
        assert not at.exception
        aller(at, "administration")
        assert not at.exception
        assert any("MODAL_URL" in i.value for i in at.info)       # onglet Dictées réelles sans GPU configuré
        assert any("Banque de textes conforme" in c.value for c in at.caption)
    finally:
        if deja is None:
            secrets.unlink()
        else:
            secrets.write_text(deja)
        for d in ("consentements/dr97.json", "profils/dr97.json"):
            (RACINE / "donnees" / d).unlink(missing_ok=True)


def test_banque_hors_convention_bloquee_et_signalee(tmp_path, monkeypatch):
    """Une phrase hors convention (modifiée sans vérification) n'est jamais proposée, et l'erreur
    s'affiche dans l'Administration."""
    import shutil
    projet = tmp_path / "projet"
    shutil.copytree(RACINE / "collecte", projet / "collecte")
    shutil.copy(RACINE / "app.py", projet / "app.py")
    shutil.copy(RACINE / "lexique.csv", projet / "lexique.csv")
    (projet / ".streamlit").mkdir()
    (projet / ".streamlit" / "secrets.toml").write_text('MODE_ACCES="mot_de_passe"\nSTOCKAGE="local"\nAPP_PASSWORD="x"\n')
    (projet / "phrases.csv").write_text(
        "id,type,modalite,texte\n"
        "ko1,phrase,Thorax,nodule pulmonaire de 12 mm.\n"
        "ok1,phrase,Thorax,absence de pneumothorax point\n", encoding="utf-8")
    monkeypatch.chdir(projet)
    import streamlit as st
    st.cache_data.clear()                             # banque() est en cache depuis le test précédent
    st.cache_resource.clear()
    at = AppTest.from_file(str(projet / "app.py")).run(timeout=60)
    at.text_input[0].input("x").run(timeout=60)
    at.sidebar.text_input[0].input("dr96").run(timeout=60)
    at.checkbox[0].check().run(timeout=60)
    at.button[0].click().run(timeout=60)
    at.button[0].click().run(timeout=60)              # « Commencer la séance »
    page = " ".join(m.value for m in at.markdown)
    assert "absence de pneumothorax" in page and "12 mm" not in page
    aller(at, "administration")
    assert not at.exception
    assert any("2 erreur(s) de convention" in e.value and "1 texte(s)" in e.value for e in at.error)


def test_onglet_a_verifier_classe_par_valeur(tmp_path, monkeypatch):
    import json
    import shutil

    import streamlit as st
    projet = tmp_path / "projet"
    shutil.copytree(RACINE / "collecte", projet / "collecte")
    for f in ("app.py", "lexique.csv", "phrases.csv"):
        shutil.copy(RACINE / f, projet / f)
    (projet / ".streamlit").mkdir()
    (projet / ".streamlit" / "secrets.toml").write_text('MODE_ACCES="mot_de_passe"\nSTOCKAGE="local"\nAPP_PASSWORD="x"\n')
    donnees = projet / "donnees"
    (donnees / "journaux").mkdir(parents=True)
    (donnees / "validations").mkdir()
    journal = [{"code": "dr01", "id_phrase": f"p{k}", "type": "phrase", "fichier": f"brut/dr01_p{k}.wav",
                "duree_s": 3.0, "texte_final": t, "horodatage": "2026-11-07T10:00:00+00:00",
                "modalite": "Sénologie", "micro": "Micro de dictée", "lieu": "Salle de lecture"}
               for k, t in enumerate(["foie de taille normale point", "opacité spiculée du sein gauche point"])]
    (donnees / "journaux" / "dr01.json").write_text(json.dumps(journal))
    (donnees / "validations" / "resultats.json").write_text(json.dumps({
        "brut/dr01_p0.wav": {"statut": "a_verifier", "raisons": ["CER > 25%"], "hypothese": "foie de taille", "cer": 0.4},
        "brut/dr01_p1.wav": {"statut": "a_verifier", "raisons": ["latéralité"], "cer": 0.3,
                             "hypothese": "Opacité spéculée du sein droit."},
    }))
    monkeypatch.chdir(projet)
    st.cache_data.clear()
    st.cache_resource.clear()                         # stockage() pointe sinon vers le test précédent
    at = AppTest.from_file(str(projet / "app.py")).run(timeout=60)
    at.text_input[0].input("x").run(timeout=60)
    at.sidebar.text_input[0].input("dr01").run(timeout=60)
    aller(at, "administration")
    assert not at.exception
    assert any(t.label == "À vérifier (2)" for t in at.tabs)          # travail en attente visible
    assert any('<mark class="ecart">spéculée</mark>' in m.value for m in at.markdown)
    choix = next(s for s in at.selectbox if s.label == "Enregistrement à vérifier")
    assert choix.options[0].startswith("+6 · terme manqué : spiculée")
    assert choix.options[1].startswith("+0 · écart global")
    assert any("Pourquoi ici" in c.value and "erreur critique" in c.value for c in at.caption)


def _projet_temporaire(tmp_path, monkeypatch, code="dr01"):
    import json
    import shutil

    import streamlit as st
    projet = tmp_path / "projet"
    shutil.copytree(RACINE / "collecte", projet / "collecte")
    for f in ("app.py", "lexique.csv", "phrases.csv"):
        shutil.copy(RACINE / f, projet / f)
    (projet / ".streamlit").mkdir()
    (projet / ".streamlit" / "secrets.toml").write_text('MODE_ACCES="mot_de_passe"\nSTOCKAGE="local"\nAPP_PASSWORD="x"\n')
    version = next(l for l in (RACINE / "app.py").read_text().splitlines() if l.startswith("VERSION_CONSENTEMENT"))
    (projet / "donnees" / "consentements").mkdir(parents=True)
    (projet / "donnees" / "consentements" / f"{code}.json").write_text(
        json.dumps({"version": version.split("=")[1].strip().strip('"\'')}))
    monkeypatch.chdir(projet)
    st.cache_data.clear()
    st.cache_resource.clear()
    return projet


def _connecter(projet, code="dr01"):
    at = AppTest.from_file(str(projet / "app.py")).run(timeout=60)
    at.text_input[0].input("x").run(timeout=60)
    at.sidebar.text_input[0].input(code).run(timeout=60)
    aller(at, "importer")
    return at


def test_import_appariement_manuel_et_modalite_par_dictee(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    import numpy as np
    import streamlit as st

    from collecte import audio as A
    projet = _projet_temporaire(tmp_path, monkeypatch)
    wav = lambda k: A.en_wav((0.05 * np.sin(np.linspace(0, 900 + k, A.FREQ * 6))).astype("float32"))
    deposes = [SimpleNamespace(name=n, getvalue=(lambda d=d: d)) for n, d in [
        ("0001.wav", wav(1)), ("0001.txt", "Nom : DIOP Amadou\nPas d'adénopathie axillaire.".encode()),
        ("DICT_17.wav", wav(2)), ("CR scanner.rtf", rb"{\rtf1\ansi Plage de verre d\'e9poli.\par}")]]
    vrai = st.file_uploader
    monkeypatch.setattr(st, "file_uploader",
                        lambda label, **kw: deposes if str(kw.get("key", "")).startswith("import_") else vrai(label, **kw))

    at = _connecter(projet)
    assert not at.exception
    assert "Associer les dictées restantes" in [s.value for s in at.subheader]
    # Avant l'association : le bouton est visible mais grisé, et la raison est écrite.
    bouton = next(b for b in at.button if b.label.startswith("Importer"))
    assert bouton.label == "Importer 1 dictée(s)" and not bouton.disabled
    assert any("1 audio(s) sans compte rendu" in c.value for c in at.caption)
    at.selectbox(key="import_0_assoc_DICT_17.wav").set_value("CR scanner.rtf").run(timeout=60)
    assert any("Pas d'adénopathie axillaire." in t.value for t in at.text)
    assert any("ligne(s) d'en-tête ignorée(s)" in c.value and "DIOP" in c.value for c in at.caption)
    at.selectbox(key="import_0_mod_1").set_value("Thorax").run(timeout=60)
    assert any("**2** dictée(s) prête(s)" in m.value for m in at.markdown)
    next(b for b in at.button if b.label == "Importer 2 dictée(s)").click().run(timeout=60)
    assert not at.exception
    assert any("2 dictée(s) importée(s) sur 2" in s.value for s in at.success)
    index = json.loads((projet / "donnees" / "historique" / "index" / "dr01.json").read_text())
    assert sorted(e["modalite"] for e in index) == ["", "Thorax"]
    textes = sorted((projet / "donnees" / "historique" / "cr" / f"{e['id']}.txt").read_text() for e in index)
    assert textes[1].startswith("Plage de verre dépoli")


def test_corriger_puis_supprimer_une_dictee_importee(tmp_path, monkeypatch):
    import json

    import numpy as np

    from collecte import audio as A
    from collecte import importation as I
    from collecte.stockage import StockageLocal
    projet = _projet_temporaire(tmp_path, monkeypatch)
    s = StockageLocal(projet / "donnees")
    ident, _ = I.enregistrer(s, "dr01", A.en_wav(np.full(A.FREQ * 6, 0.01, dtype="float32")), 6.0,
                             "Pas d'adénopathie.", "Sénologie", "radiologue")
    at = _connecter(projet)
    assert [e.label for e in at.expander if e.label.startswith("Corriger")] == ["Corriger ou supprimer une dictée importée"]
    at.selectbox(key=f"corr_mod_{ident}").set_value("Thorax").run(timeout=60)
    next(b for b in at.button if b.label == "Enregistrer la modalité").click().run(timeout=60)
    assert I.index_de(s, "dr01")[0]["modalite"] == "Thorax"
    at.text_area(key=f"corr_texte_{ident}").input("IRM du genou droit. Ménisque normal.").run(timeout=60)
    next(b for b in at.button if b.label == "Remplacer le compte rendu").click().run(timeout=60)
    assert s.lire(f"historique/cr/{ident}.txt").decode() == "IRM du genou droit. Ménisque normal."
    assert any("ré-étiquetée" in m.value for m in at.success)
    supprimer = next(b for b in at.button if b.label == "Supprimer la dictée")
    assert supprimer.disabled                                     # confirmation obligatoire
    at.checkbox(key=f"corr_confirme_{ident}").check().run(timeout=60)
    next(b for b in at.button if b.label == "Supprimer la dictée").click().run(timeout=60)
    assert not at.exception
    assert I.index_de(s, "dr01") == [] and s.lire(f"historique/audio/{ident}.wav") is None
    assert json.loads((projet / "donnees" / "historique" / "index" / "dr01.json").read_text()) == []


def test_bouton_importer_toujours_visible_avec_la_raison(tmp_path, monkeypatch):
    """Noms différents, rien d'associé : le bouton est grisé et la page dit pourquoi."""
    from types import SimpleNamespace

    import numpy as np
    import streamlit as st

    from collecte import audio as A
    projet = _projet_temporaire(tmp_path, monkeypatch)
    wav = A.en_wav((0.05 * np.sin(np.linspace(0, 900, A.FREQ * 6))).astype("float32"))
    deposes = [SimpleNamespace(name="DICT_17.wav", getvalue=lambda: wav),
               SimpleNamespace(name="CR Diop.txt", getvalue=lambda: b"Pas d'adenopathie.")]
    vrai = st.file_uploader
    monkeypatch.setattr(st, "file_uploader",
                        lambda label, **kw: deposes if str(kw.get("key", "")).startswith("import_") else vrai(label, **kw))
    at = _connecter(projet)
    bouton = next(b for b in at.button if b.label.startswith("Importer"))
    assert bouton.label == "Importer 0 dictée(s)" and bouton.disabled
    assert any("1 audio(s) sans compte rendu" in c.value for c in at.caption)
    at.selectbox(key="import_0_assoc_DICT_17.wav").set_value("Coller le texte du compte rendu").run(timeout=60)
    assert next(b for b in at.button if b.label.startswith("Importer")).disabled
    assert any("texte du compte rendu à coller" in c.value for c in at.caption)
    at.text_area(key="import_0_colle_0").input("Pas d'adénopathie axillaire.").run(timeout=60)
    bouton = next(b for b in at.button if b.label.startswith("Importer"))
    assert bouton.label == "Importer 1 dictée(s)" and not bouton.disabled


def test_duree_lisible():
    import re
    source = (RACINE / "app.py").read_text()
    code = re.search(r"def duree_lisible.*?\n(?=\n\n)", source, re.S).group(0)
    espace = {}
    exec(code, espace)
    f = espace["duree_lisible"]
    assert [f(45), f(125), f(3600 * 3 + 300), f(None)] == ["45 s", "2 min", "3 h 05", "0 s"]
