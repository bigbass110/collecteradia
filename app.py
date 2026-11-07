"""Point d'entrée de Streamlit Community Cloud.

Le code de l'application est dans collecte-dictees/app.py : c'est la SEULE source. Ce fichier ne fait
que la lancer, pour que le réglage « Main file path » de Streamlit Cloud (app.py) n'ait pas à changer.
N'ajoutez rien ici.
"""
import runpy
import sys
from pathlib import Path

PROJET = Path(__file__).resolve().parent / "collecte-dictees"
if str(PROJET) not in sys.path:
    sys.path.insert(0, str(PROJET))
runpy.run_path(str(PROJET / "app.py"), run_name="__main__")
