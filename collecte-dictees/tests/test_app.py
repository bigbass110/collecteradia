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
    finally:
        if deja is None:
            secrets.unlink()
        else:
            secrets.write_text(deja)
        for d in ("consentements/dr97.json", "profils/dr97.json"):
            (RACINE / "donnees" / d).unlink(missing_ok=True)
