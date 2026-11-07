# RadIA · Collecte de dictées

Tout le projet est dans [`collecte-dictees/`](collecte-dictees/LISEZMOI.md) : lisez son LISEZMOI.

À la racine ne restent que les fichiers que les services lisent à cet endroit :

| Fichier | Lu par | Rôle |
| --- | --- | --- |
| `app.py` | Streamlit Community Cloud | Lance `collecte-dictees/app.py` ; n'y ajoutez rien |
| `requirements.txt`, `packages.txt` | Streamlit Community Cloud | Dépendances ; doivent rester identiques à celles de `collecte-dictees/` (vérifié par les tests) |
| `.streamlit/config.toml` | Streamlit Community Cloud | Thème et réglages ; identique à celui de `collecte-dictees/` (vérifié par les tests) |
| `.github/workflows/verifications.yml` | GitHub Actions | Vérifie la banque de textes et lance les tests à chaque envoi |
| `.githooks/pre-push` | Git (après `git config core.hooksPath .githooks`) | Les mêmes vérifications avant chaque envoi |
