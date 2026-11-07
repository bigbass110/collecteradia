"""Organisation du dépôt : une seule copie du code, dans collecte-dictees/."""
from pathlib import Path

from streamlit.testing.v1 import AppTest

PROJET = Path(__file__).resolve().parents[1]
DEPOT = PROJET.parent


def _lignes(chemin: Path) -> set:
    return {l.strip() for l in chemin.read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.strip().startswith("#")}


def test_aucune_copie_du_code_a_la_racine():
    assert sorted(p.name for p in DEPOT.glob("*.py")) == ["app.py"]
    assert len((DEPOT / "app.py").read_text(encoding="utf-8").splitlines()) < 20      # lanceur seulement


def test_dependances_identiques_a_la_racine():
    for nom in ("requirements.txt", "packages.txt"):
        assert _lignes(DEPOT / nom) == _lignes(PROJET / nom), f"{nom} diffère entre la racine et collecte-dictees/"


def test_le_lanceur_de_la_racine_ouvre_l_application(monkeypatch):
    import streamlit as st
    monkeypatch.chdir(DEPOT)
    st.cache_data.clear()
    st.cache_resource.clear()
    at = AppTest.from_file(str(DEPOT / "app.py"))
    at.secrets["MODE_ACCES"] = "mot_de_passe"
    at.secrets["STOCKAGE"] = "local"
    at.secrets["APP_PASSWORD"] = "x"
    at.run(timeout=60)
    at.text_input[0].input("x").run(timeout=60)
    assert not at.exception
    at.sidebar.text_input[0].input("dr94").run(timeout=60)
    assert not at.exception
    assert [s.value for s in at.subheader] == ["Consentement"]
