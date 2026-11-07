# Registre de suivi

État des blocages et des actions en attente. Une ligne se met à jour quand l'état change ; une action
terminée passe dans « Fait », avec sa date.

## Blocage en cours

| Depuis | Blocage | Effet | Levée |
| --- | --- | --- | --- |
| 2026-11-07 | **Modal : aucun moyen de paiement enregistré** dans l'espace `abassbaz97`. Modal refuse tout GPU sans carte (« Please add a payment method to use L4 GPU functions », de même pour le T4), même avec des crédits restants | Le programme d'étiquetage GPU n'est pas déployé : le bouton « Lancer l'étiquetage » de l'application ne peut pas fonctionner | Ajouter une carte (Settings → Usage & Billing) et **fixer une limite de dépenses inférieure ou égale aux crédits** : les crédits sont consommés d'abord, la carte n'est pas débitée tant qu'il en reste |

### État du déploiement au moment du blocage

- Client Modal 1.2.6 installé sur le Mac (`python3 -m modal`), relié à l'espace `abassbaz97`.
- Secret Modal créé sous le nom **`custom-secret`** (R2 + `RADIA_JETON`) ; `gpu/modal_etiquetage.py` l'utilise.
- **Image GPU déjà construite** et enregistrée chez Modal (NeMo, faster-whisper, Parakeet et Whisper
  téléchargés) : le prochain déploiement ne la reconstruit pas.
- Le Mac n'a que Python 3.9 : le client Modal le plus récent pour cette version (1.2.6) ne connaît pas
  la version d'images `2026.10` de l'espace. Contournement : `MODAL_IMAGE_BUILDER_VERSION=2025.06`.

### Reprise, dès que la carte est active

```bash
cd collecte-dictees
MODAL_IMAGE_BUILDER_VERSION=2025.06 python3 -m modal deploy gpu/modal_etiquetage.py
```

Puis :
1. Copier l'adresse du point d'entrée `lancer` affichée à la fin du déploiement.
2. Streamlit Cloud → Settings → Secrets : `MODAL_URL = "<adresse>"` et `MODAL_JETON = "<RADIA_JETON du secret>"`.
3. Importer 2 ou 3 dictées, Administration → « Dictées réelles » → « Lancer l'étiquetage ».
4. Vérifier : segments dans « À vérifier », `ponctuation_moteur`, concordance, noms propres signalés.
5. Mettre ce registre à jour (blocage levé, date).

## Actions en attente

| Action | Dépend de | Responsable |
| --- | --- | --- |
| Déployer l'étiquetage GPU et relier l'application (`MODAL_URL`, `MODAL_JETON`) | Blocage Modal ci-dessus | Coordinateur |
| Étiqueter 2 ou 3 dictées et relever `ponctuation_moteur`, la concordance, les noms signalés | Déploiement GPU | Coordinateur |
| Régler le seuil de concordance (0,3 → 0,5 ?) | Concordances des premières dictées | Code |
| Donner plus de poids aux dictées réelles à l'entraînement | Volume de dictées réelles étiquetées | Code |
| Mesurer Parakeet de base (`scripts/evaluer.py`) | Premier instantané avec jeux de test | Machine GPU |
| Faire grossir la banque de phrases (`construire_lexique.py`, `generer_phrases.py`) | Premières validations et dictées étiquetées | Coordinateur + radiologue relecteur |
| Facultatif : installer Python 3.12 sur le Mac (client Modal à jour, tests dans le crochet `pre-push`) | — | Coordinateur |

## Fait

| Date | Action |
| --- | --- |
| 2026-11-07 | Transfert des données hors du Sénégal validé par la CDP |
| 2026-11-07 | Application en ligne sur Streamlit Cloud, import des dictées vérifié (bouton « Importer ») |
| 2026-11-07 | GitHub Actions remis en marche, vérifications au vert |
| 2026-11-07 | Image GPU construite chez Modal (déploiement bloqué ensuite par le paiement) |
