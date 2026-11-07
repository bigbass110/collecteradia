from pathlib import Path

from streamlit.testing.v1 import AppTest

RACINE = Path(__file__).resolve().parents[1]


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
        at.sidebar.radio[0].set_value("Importer des dictées").run(timeout=60)
        assert at.title[0].value == "Importer des dictées"
        assert not at.exception
        at.sidebar.radio[0].set_value("Administration").run(timeout=60)
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
    at.sidebar.radio[0].set_value("Administration").run(timeout=60)
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
    at.sidebar.radio[0].set_value("Administration").run(timeout=60)
    assert not at.exception
    choix = next(s for s in at.selectbox if s.label == "Enregistrement à vérifier")
    assert choix.options[0].startswith("+6 · terme manqué : spiculée")
    assert choix.options[1].startswith("+0 · écart global")
    assert any("Pourquoi ici" in c.value and "erreur critique" in c.value for c in at.caption)
