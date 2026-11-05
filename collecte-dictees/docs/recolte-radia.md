# Récolte des dictées réelles depuis RadIA (P2)

> **Déjà disponible** : l'import manuel des dictées historiques (page « Importer des dictées ») et leur
> étiquetage automatique sur GPU. Ce document spécifie l'étape suivante : la récolte **automatique et
> continue** depuis RadIA, sans import manuel.

L'application de collecte amorce le premier modèle. À terme, la meilleure source de données est
**RadIA en usage réel** : dictées spontanées, vrais micros, vrais termes. Ce document spécifie ce
que RadIA doit produire pour que ces données rejoignent le même stockage et les mêmes manifestes.
L'implémentation se fait dans le dépôt RadIA.

## Conditions préalables

1. **Consentement distinct du soin**, recueilli dans RadIA (réglages du radiologue), versionné,
   révocable. Sans consentement actif : rien n'est récolté.
2. **Cadre CDP validé** pour cette finalité.
3. **Aucune donnée nominative** : l'identité patient est déjà chiffrée côté client (E2EE) et ne
   doit jamais apparaître dans l'audio exporté ni dans le texte. Les dictées où un nom est
   prononcé sont exclues (détection par le registre de constats + revue humaine).

## Ce qui est récolté

**La paire « audio brut → transcription verbatim corrigée », jamais le compte rendu signé.**
Le compte rendu final est restructuré ; l'utiliser comme cible apprendrait au modèle à inventer.

- Audio : la dictée telle que captée (file locale chiffrée de RadIA), convertie en WAV mono 16 kHz.
- Texte : la sortie brute du moteur, **corrigée par le radiologue avant toute structuration**,
  selon `docs/convention-ecriture.md` (commandes en toutes lettres). RadIA doit donc conserver
  la transcription brute et l'état « relu » de cette transcription.
- Moment : uniquement après **signature** du compte rendu (le radiologue a relu).

## Format de dépôt (même stockage R2, même logique)

```text
brut/radia_<code>_<identifiant-aleatoire>.wav / .txt
journaux/radia_<code>.json   entrées identiques à l'application, avec en plus :
    "source": "radia", "type": "dictee_reelle", "moteur": "whisper-large-v3-turbo",
    "transcription_brute": "...",  "modalite": "...", "micro": "...", "lieu": "..."
```

`<identifiant-aleatoire>` ne doit dériver ni du patient, ni de l'examen, ni de la date.

## Traitement avant entraînement

1. Validation automatique (`scripts/valider_enregistrements.py`) puis revue des cas signalés.
2. Découpage en segments de 5 à 20 s par alignement forcé (NeMo Forced Aligner).
3. Nouvel ensemble `train_reel` dans les instantanés ; un `test_reel` réservé, figé, par voix.
4. Comparaison systématique : modèle entraîné sur lecture seule vs lecture + réel.

## Indicateur de pilotage

Le **taux de corrections par compte rendu** dans RadIA, avant et après chaque nouveau modèle.
C'est le bénéfice que ressentent les radiologues.
