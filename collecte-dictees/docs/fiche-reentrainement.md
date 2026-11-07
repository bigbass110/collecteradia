# Fiche de ré-entraînement de Parakeet (NeMo)

Modèle de départ : `nvidia/parakeet-tdt-0.6b-v3`. Objectif : l'adapter au vocabulaire, aux voix et
à la convention d'écriture de RadIA, **sans dégrader** ce qu'il sait déjà faire.

Un ré-entraînement suit toujours le même ordre : **instantané → mesure de base → essai à blanc →
entraînement → évaluation → décision**. On ne saute aucune étape, et chaque modèle est noté au
registre (dernière section).

> Les commandes NeMo ci-dessous suivent le script officiel `speech_to_text_finetune.py`. Les noms de
> paramètres peuvent varier d'une version de NeMo à l'autre : l'**essai à blanc** (étape 3) sert à le
> vérifier avant de lancer un entraînement long.

## 1. Préparer les données

```bash
python scripts/telecharger_donnees.py /data/radia --fractions 0.25,0.5
export INST=/data/radia/<instantane>
cat $INST/resume.json
```

Dans `resume.json`, vérifiez avant d'aller plus loin :

| Point | Seuil indicatif | Si ce n'est pas atteint |
| --- | --- | --- |
| Heures de `train` | 5 h au minimum ; gains nets attendus vers 20 à 50 h | Continuer la collecte ; un entraînement sur 1 ou 2 h apprend surtout par cœur |
| Voix dans `train` | 5 au minimum, 10 ou plus idéalement | Le modèle risque de ne bien marcher que pour ces voix |
| Énoncés par jeu de test | 300 ou plus, 5 groupes ou plus | `evaluer.py` le signale ; ne pas conclure sur un jeu trop petit |
| `exclus.a_verifier` | Faible devant `train` | Trancher les cas dans l'onglet « À vérifier », puis refaire un instantané |

**Ne jamais** entraîner sur un fichier `test_*.json`, ni copier des lignes de test dans `train`.
Les jeux de test d'un instantané ne servent qu'à `evaluer.py`.

### Données générales (recommandé)

Un modèle ré-entraîné uniquement sur de la radiologie peut se dégrader sur le reste du français
(« oubli »). On ajoute donc **20 à 30 %** (en heures) de français général, ramené à la même
convention :

```bash
# Common Voice français (dossier contenant clips/ et train.tsv)
python scripts/preparer_general.py $INST --format cv --source ~/cv-corpus/fr
# ou Multilingual LibriSpeech français (dossier contenant transcripts.txt et audio/)
python scripts/preparer_general.py $INST --format mls --source ~/mls_french/train
```

Le script :
- met le texte en minuscules sans ponctuation, et **rejette** tout énoncé avec un chiffre, un nombre en
  lettres, un sigle, une abréviation ou un symbole (on ignore comment le lecteur les a prononcés) ;
- met de côté **300 énoncés de voix jamais entraînées** dans `general_test.json`, pour vérifier
  l'absence de régression (étape 5) ;
- garde au plus 50 énoncés par voix, en alternant les voix, et des durées de 1 à 20 s ;
- écrit `train_mixte.json` (et `train_25_mixte.json`, `train_50_mixte.json`) : la radiologie + 25 %
  de français général (`--proportion` pour changer). **C'est ce fichier qu'on entraîne.**

Le bilan est dans `general_resume.json`. Relancer le script reprend sans refaire l'audio déjà converti.

## 2. Mesurer le modèle de base

```bash
python scripts/evaluer.py /data/radia/<instantane>
```

Notez les résultats au registre. Tout gain se mesure **par rapport à ce point de départ**, sur le même
instantané.

## 3. Essai à blanc (5 minutes)

Il vérifie que les manifestes, la mémoire GPU et les paramètres sont acceptés par votre version de
NeMo, avant d'y consacrer des heures de GPU.

```bash
git clone --depth 1 https://github.com/NVIDIA/NeMo.git ~/NeMo        # pour le script d'exemple
pip install "nemo_toolkit[asr]"

export INST=/data/radia/<instantane>
python ~/NeMo/examples/asr/speech_to_text_finetune.py \
    --config-path=conf/asr_finetune --config-name=speech_to_text_finetune \
    init_from_pretrained_model=nvidia/parakeet-tdt-0.6b-v3 \
    model.train_ds.manifest_filepath=$INST/train_25_mixte.json \
    model.validation_ds.manifest_filepath=$INST/val.json \
    model.tokenizer.update_tokenizer=false \
    model.train_ds.batch_size=8 model.validation_ds.batch_size=8 \
    trainer.devices=1 trainer.accelerator=gpu trainer.precision=bf16-mixed \
    trainer.max_steps=20 trainer.val_check_interval=10 \
    exp_manager.exp_dir=/modeles/essais exp_manager.name=essai_a_blanc
```

L'essai est réussi si l'entraînement démarre, si la perte (`train_loss`) diminue et si `val_wer`
s'affiche. Erreurs fréquentes :

| Message | Cause probable | Correction |
| --- | --- | --- |
| `CUDA out of memory` | Lot trop grand | Diviser `batch_size` par 2 et doubler `trainer.accumulate_grad_batches` |
| Clé de configuration inconnue | Version de NeMo différente | Ajouter `+` devant la clé (`+init_from_pretrained_model=...`), ou consulter `speech_to_text_finetune.yaml` de votre version |
| Fichier audio introuvable | Manifeste non téléchargé sur cette machine | Relancer `telecharger_donnees.py` sur la machine d'entraînement |
| `val_wer` proche de 100 % dès le départ | Texte des manifestes illisible ou vide | Ouvrir `val.json` et vérifier le champ `text` |

## 4. Entraîner

### Réglages

| Paramètre | Valeur | Pourquoi |
| --- | --- | --- |
| Tokenizer | **conservé** (`update_tokenizer=false`) | Il sait déjà écrire « BI-RADS », « spiculée » ; un nouveau tokenizer effacerait l'apprentissage du décodeur |
| Taux d'apprentissage | `3e-5` ; `1e-5` sous 10 h | 30 à 100 fois plus bas que le pré-entraînement : on ajuste, on ne réapprend pas |
| Montée progressive (warmup) | 10 % des pas | Évite de casser le modèle aux premiers pas |
| Planification | CosineAnnealing, `min_lr=1e-6` | Décroissance douce jusqu'à la fin |
| Optimiseur | AdamW, `weight_decay=1e-3` | Réglage standard pour un ajustement fin |
| Époques | 5 à 10 | Au-delà, le risque d'apprendre les phrases par cœur augmente |
| Précision | `bf16-mixed` | Deux fois moins de mémoire, sans perte de qualité sur L4, A100 et H100 |
| Lot effectif | 32 énoncés (`batch_size × accumulate_grad_batches`) | L4 24 Go : 8 × 4 ; A100 80 Go : 32 × 1 |
| SpecAugment | celui du modèle, inchangé | Déjà adapté ; pas besoin d'y toucher |

**Nombre de pas** : `max_steps = époques × énoncés de train ÷ lot effectif`. Le nombre d'énoncés est
`general_resume.json → mixtes.train_mixte` (ou `resume.json → ensembles.train.enregistrements` sans
données générales).

| Énoncés de train | Lot effectif | Époques | `max_steps` | `warmup_steps` |
| --- | --- | --- | --- | --- |
| 5 000 (environ 7 h) | 32 | 8 | 1 250 | 125 |
| 15 000 (environ 20 h) | 32 | 6 | 2 800 | 280 |
| 40 000 (environ 55 h) | 32 | 5 | 6 250 | 625 |

### Commande

```bash
export INST=/data/radia/<instantane>
export NOM=radia_<instantane>_train_v1           # voir « Nommer un modèle »
python ~/NeMo/examples/asr/speech_to_text_finetune.py \
    --config-path=conf/asr_finetune --config-name=speech_to_text_finetune \
    init_from_pretrained_model=nvidia/parakeet-tdt-0.6b-v3 \
    model.train_ds.manifest_filepath=$INST/train_mixte.json \
    model.validation_ds.manifest_filepath=$INST/val.json \
    model.train_ds.max_duration=20 \
    model.tokenizer.update_tokenizer=false \
    model.train_ds.batch_size=8 model.validation_ds.batch_size=8 \
    trainer.accumulate_grad_batches=4 \
    model.optim.name=adamw model.optim.lr=3e-5 model.optim.weight_decay=1e-3 \
    model.optim.sched.name=CosineAnnealing model.optim.sched.warmup_steps=280 model.optim.sched.min_lr=1e-6 \
    trainer.devices=1 trainer.accelerator=gpu trainer.precision=bf16-mixed \
    trainer.max_steps=2800 trainer.val_check_interval=200 \
    exp_manager.exp_dir=/modeles exp_manager.name=$NOM \
    exp_manager.checkpoint_callback_params.monitor=val_wer \
    exp_manager.checkpoint_callback_params.mode=min \
    exp_manager.checkpoint_callback_params.save_top_k=3
```

Le meilleur modèle (le plus faible `val_wer`) est enregistré en `.nemo` dans
`/modeles/$NOM/<date>/checkpoints/`.

### Surveiller pendant l'entraînement

- **`val_wer` remonte** alors que `train_loss` continue de baisser : le modèle apprend par cœur.
  Gardez le meilleur point (fait automatiquement), et réduisez les époques la prochaine fois.
- **`val_wer` ne baisse pas du tout** : taux d'apprentissage trop bas, ou données trop proches de ce
  que le modèle sait déjà. Regardez `construire_lexique.py` : y a-t-il vraiment des erreurs à corriger ?
- **`train_loss` explose ou devient `nan`** : taux d'apprentissage trop haut. Divisez-le par 3.

### Courbe d'apprentissage

Entraînez avec exactement les mêmes réglages sur `train_25_mixte.json`, `train_50_mixte.json` et
`train_mixte.json`, en adaptant `max_steps` au nombre d'énoncés (voir `general_resume.json`).
Sans données générales, utilisez `train_25.json`, `train_50.json` et `train.json`. Évaluez les trois modèles. Si le WER baisse encore nettement
entre `train_50` et `train`, la collecte vaut la peine d'être poursuivie. S'il ne baisse plus, mieux
vaut cibler d'autres termes ou d'autres voix que d'ajouter des heures.

## 5. Évaluer

```bash
python scripts/evaluer.py $INST --modele nvidia/parakeet-tdt-0.6b-v3 \
    --modele /modeles/$NOM/<date>/checkpoints/$NOM.nemo
```

Pour un modèle entraîné sur une fraction, indiquez son manifeste : `--train $INST/train_50.json`.
Sinon `train.json` sert à repérer les phrases répétées.

### Critères pour mettre un modèle en service

Tous doivent être remplis.

| Critère | Où le lire |
| --- | --- |
| L'écart de « WER hors commandes » est négatif et son intervalle ne contient pas 0, sur `test_voix_nouvelles` et sur `test_historique` | Ligne « écart avec la référence » |
| Sur `test_historique`, l'écart des « phrases nouvelles » est lui aussi négatif : le gain ne vient pas seulement des phrases types répétées de l'entraînement | Ligne « WER phrases nouvelles », et son écart |
| Aucune hausse des erreurs de négation, de latéralité ou de nombres, sur aucun jeu | Lignes « Négation fausse », « Latéralité fausse », « Nombres faux » |
| Rappel des commandes de 95 % ou plus | Ligne « Commandes : rappel » |
| Rappel des termes rares et des paires dangereuses en hausse | Lignes « Termes » |
| Pas de régression sur le français général : l'écart de WER ne doit pas être positif au-delà de son intervalle | `evaluer.py $INST --ensembles general_test --modele <base> --modele <nouveau.nemo>` |
| Erreurs critiques écoutées par un radiologue | Champ `critiques` du rapport JSON |

`test_voix_nouvelles` est le meilleur prédicteur pour un radiologue qui n'a jamais participé.
`test_historique` est le plus proche de la pratique réelle. `test_voix_connues` seul ne suffit pas :
il surestime le gain.

## 6. Mettre en service dans RadIA

- Le modèle ré-entraîné **n'écrit plus la ponctuation** : il écrit « virgule », « point à la ligne »…
  en toutes lettres. Il doit être déployé **avec** le formatage vocal de RadIA, qui convertit ces
  commandes. Vérifiez que la liste `COMMANDES` de `collecte/texte.py` est identique à celle de RadIA.
- Gardez le modèle précédent disponible : en cas de problème signalé par les radiologues, on revient
  en arrière en changeant de fichier `.nemo`.
- Suivez ensuite le **taux de corrections par compte rendu** dans RadIA, avant et après le changement
  (voir `docs/recolte-radia.md`).

## Nommer un modèle et tenir le registre

Nom : `radia_<instantane>_<fraction>_v<n>`, par exemple `radia_2026-11-05_101500_train50_v1`.
On ne réutilise jamais un nom.

Pour chaque entraînement, ajoutez une ligne à `modeles/REGISTRE.md` (sur la machine d'entraînement ou
dans le dépôt, sans aucune donnée) :

| Champ | Exemple |
| --- | --- |
| Modèle | `radia_2026-11-05_101500_train_v1` |
| Date, auteur | 2026-11-20, coordinateur |
| Instantané, fraction | `2026-11-05_101500`, `train` |
| Données | 18,4 h, 14 voix, 13 200 énoncés ; + 6,1 h de Common Voice (proportion 0,25) |
| NeMo, GPU | `nemo_toolkit` 2.x.y, 1 × L4 |
| Réglages | lr 3e-5, 2 800 pas, lot 8 × 4, bf16 |
| Meilleur `val_wer` | 6,1 % au pas 2 200 |
| Évaluation | rapport `evaluation_2026-11-20_….json` : WER hors commandes 14,2 % → 7,9 % [−7,4 ; −5,1] sur `test_voix_nouvelles` |
| Décision | Mis en service / Refusé (raison) |

La version de NeMo s'obtient avec `pip show nemo_toolkit`. Notez-la toujours : un modèle se
recharge de façon fiable avec la version qui l'a produit.
