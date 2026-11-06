# RadIA · Collecte de dictées (v2)

Application web pour constituer le corpus de ré-entraînement de Parakeet, à partir de deux sources :

- **les dictées réelles** déjà réalisées en pratique, importées avec leur compte rendu, puis
  étiquetées automatiquement sur GPU (source principale) ;
- **les lectures** : phrases courtes et comptes rendus complets lus par les radiologues, pour les
  termes rares, les nouvelles voix et des jeux de test à vérité exacte.

```text
Dépôt GitHub ──déploiement──▶ Streamlit Community Cloud ◀── radiologues (connexion Google)
     │                                  │
     └─ GitHub Actions :                ▼
        convention + tests      Cloudflare R2 (voix, textes, journaux, instantanés)
                                        │
            machine GPU : valider_enregistrements.py ──▶ onglet « À vérifier »
                          telecharger_donnees.py     ──▶ NeMo (ré-entraînement)
```

## Le cycle de travail

1. **Collecter** : les radiologues enregistrent des lectures, et importent leurs dictées réelles
   (page « Importer des dictées ») ; le coordinateur lance l'étiquetage sur GPU
   (Administration → « Dictées réelles »).
2. **Valider** : sur une machine GPU, `scripts/valider_enregistrements.py` transcrit chaque
   enregistrement avec Parakeet de base et signale les écarts (négation, latéralité, nombres, CER).
3. **Vérifier** : le coordinateur écoute les cas signalés (onglet « À vérifier ») et tranche :
   conforme, corrigé ou rejeté.
4. **Figer** : onglet « Jeux de données » → **Créer un instantané**. Il n'est jamais modifié.
5. **Entraîner** : `scripts/telecharger_donnees.py` récupère l'instantané ; on entraîne avec la
   fiche de ré-entraînement en citant le nom de l'instantané.

## Ce que fait l'application

| Fonction | Détail |
| --- | --- |
| Commandes de ponctuation | Affichées en couleur dans le texte, dictées et transcrites en toutes lettres (voir `docs/convention-ecriture.md`) |
| Deux types de lecture | Phrases courtes (1 à 20 s) ; comptes rendus complets (jusqu'à 6 min), réservés au test réaliste |
| Séance | Micro et lieu déclarés au début (langue maternelle facultative), objectif de textes affiché |
| Sélection intelligente | Propose d'abord les textes contenant les termes du lexique les moins couverts |
| Contrôles audio | Rognage des silences, détection d'un début ou d'une fin coupés, saturation, silence, durée |
| Correction de texte | Vérifiée par le correcteur de convention avant enregistrement |
| Voix réservées au test | Les codes de `TEST_LOCUTEURS` ne lisent que les phrases du test et n'entrent jamais en entraînement |
| Répartition | Déterministe par identifiant de phrase (80/10/10) : une phrase ne change jamais d'ensemble |
| Instantanés | `train`, `val`, `test_voix_connues`, `test_voix_nouvelles`, `test_comptes_rendus`, `test_historique` + résumé (heures, voix, sources, versions) |
| Traçabilité | Chaque enregistrement garde texte attendu et final, micro, lieu, navigateur, versions de la banque et de l'application |
| Import de dictées réelles | Paires audio + compte rendu (txt, docx, pdf), associées par nom de fichier ; noms d'origine jamais conservés ; doublons refusés |
| Étiquetage sur GPU | Parakeet + Whisper + compte rendu ; corrections confirmées par deux sources ; texte de modèle ignoré ; passages avec nom, date ou âge exclus ; segments de 1,5 à 20 s |
| Administration | Vue d'ensemble, couverture du lexique, à vérifier, écoute et suppression, dictées réelles, instantanés, retrait de consentement |

## Contenu du dépôt

| Fichier | Rôle |
| --- | --- |
| `app.py` | L'interface Streamlit |
| `collecte/` | Logique testable : stockage, audio, convention d'écriture, instantanés, import, étiquetage |
| `gpu/modal_etiquetage.py` | Programme GPU (Modal) déclenché par l'application pour étiqueter les dictées réelles |
| `phrases.csv` | Banque de textes : `id`, `type` (`phrase` ou `compte_rendu`), `modalite`, `texte` |
| `lexique.csv` | Termes cibles et leur catégorie (scores, paires dangereuses, négations...) |
| `scripts/verifier_phrases.py` | Correcteur de la banque (lancé par GitHub Actions) |
| `scripts/valider_enregistrements.py` | Validation automatique avec Parakeet de base (machine GPU) |
| `scripts/telecharger_donnees.py` | Téléchargement d'un instantané, sous-ensembles pour la courbe d'apprentissage |
| `tests/` | Tests automatiques (`pytest`) |
| `docs/convention-ecriture.md` | La règle d'écriture, source unique |
| `docs/recolte-radia.md` | Spécification de la récolte des dictées réelles depuis RadIA |
| `.github/workflows/verifications.yml` | Vérifie la banque et lance les tests à chaque envoi |

## Mise en ligne

### 1. Dépôt GitHub (privé)

```bash
git init && git add . && git commit -m "Collecte de dictées v2"
git branch -M main
git remote add origin https://github.com/<votre-compte>/radia-collecte.git
git push -u origin main
```

Vérifiez sur GitHub que `.streamlit/secrets.toml` n'y est **pas**, et que l'onglet **Actions**
affiche les vérifications au vert.

### 2. Cloudflare R2

1. Compte Cloudflare → **R2 Object Storage** (un moyen de paiement peut être demandé même pour
   l'offre gratuite de 10 Go, soit environ 85 heures d'audio).
2. Compartiment `radia-collecte` ; si une juridiction (Union européenne) est proposée, choisissez-la.
3. Trois jetons API limités au compartiment :
   - **lecture et écriture** pour l'application ;
   - **lecture et écriture** pour `valider_enregistrements.py` (il écrit `validations/resultats.json`) ;
   - **lecture seule** pour `telecharger_donnees.py`.

### 3. Connexion Google

Google Cloud Console → nouveau projet → **Écran de consentement OAuth** (type Externe ; en mode
Test, ajoutez les e-mails des radiologues comme utilisateurs de test) → **Identifiants** → **ID client
OAuth**, type Application Web, URI de redirection `https://<votre-app>.streamlit.app/oauth2callback`.

### 4. Streamlit Community Cloud

share.streamlit.io → **Create app** → dépôt, branche `main`, fichier `app.py` → **Advanced
settings** : Python 3.12, et dans **Secrets** le contenu de `.streamlit/secrets.toml.exemple`
rempli. `cookie_secret` : `python -c "import secrets; print(secrets.token_hex(32))"`.

Une application configurée avec une connexion compte comme l'unique application privée
autorisée par espace de travail sur Community Cloud.

## Dictées réelles : import et étiquetage sur GPU

### Ce qui se passe

1. Un radiologue (ou le coordinateur, pour son compte) dépose ses dictées et leurs comptes rendus
   dans **« Importer des dictées »**, par lots de 20 au plus. L'audio est converti en 16 kHz ;
   le compte rendu en texte. Le nom de fichier d'origine est remplacé par un identifiant aléatoire.
2. Le coordinateur clique sur **« Lancer l'étiquetage »** (Administration → « Dictées réelles »).
   Un GPU démarre chez Modal, traite toutes les dictées en attente, puis s'éteint.
3. Pour chaque dictée : transcription par Parakeet et par Whisper, rapprochement avec le compte
   rendu, découpage en segments. Chaque segment est classé :
   - **fiable** : les sources concordent (ou une correction du compte rendu est confirmée par Whisper) ;
   - **à vérifier** : désaccord, mot entendu par un seul moteur, correction non confirmée,
     compte rendu peut-être mal apparié ;
   - **exclu** : un nom, une date ou un âge y est prononcé.
4. Les segments à vérifier apparaissent dans **« À vérifier »**, avec les deux transcriptions.
5. Une dictée sur dix (et toutes celles des voix de `TEST_LOCUTEURS`) est **réservée au test** :
   tous ses segments sont à relire, et seuls les segments relus entrent dans `test_historique`.
   Les segments d'une même dictée restent toujours dans le même ensemble.

Le texte du compte rendu n'est **jamais** ajouté s'il n'a pas été prononcé : il sert à corriger
les termes que les moteurs ont mal entendus, pas à remplacer la dictée.

### Mettre en place le GPU (une fois)

1. Créez un compte sur modal.com. Dans les réglages de l'espace de travail, **fixez une limite de
   dépenses**. La facturation est à la seconde de GPU ; vérifiez la grille tarifaire et les éventuels
   crédits gratuits mensuels.
2. Créez dans R2 un jeton **lecture et écriture** limité au compartiment, réservé au programme GPU.
3. Sur votre ordinateur, à la racine du dépôt :

```bash
pip install modal
modal setup
python -c "import secrets; print(secrets.token_hex(32))"      # notez cette chaîne : RADIA_JETON
modal secret create radia-etiquetage \
    R2_ACCOUNT_ID=... R2_ACCESS_KEY_ID=... R2_SECRET_ACCESS_KEY=... R2_BUCKET=radia-collecte \
    RADIA_JETON=<la-chaine>
modal deploy gpu/modal_etiquetage.py
```

   Le premier déploiement est long : il construit une image avec NeMo, Whisper et les deux modèles.
4. Modal affiche l'adresse du point d'entrée `lancer`. Ajoutez dans les Secrets de l'application :
   `MODAL_URL = "<cette adresse>"` et `MODAL_JETON = "<la même chaîne que RADIA_JETON>"`.
5. Après toute modification de `collecte/` ou de `lexique.csv`, relancez `modal deploy gpu/modal_etiquetage.py`.

### Avant de traiter tout l'historique

- **Commencez par 2 ou 3 dictées** : vérifiez dans « À vérifier » et « Enregistrements » que les
  segments sont justes, et notez le temps de traitement pour estimer le coût du reste.
- **Commandes dictées** : vérifiez que Parakeet écrit bien « virgule » ou « point à la ligne » en
  toutes lettres quand ils sont dictés. S'il les remplace par des signes, les segments perdront ces
  commandes : il faudra alors corriger à la relecture ou adapter l'étiquetage.
  Chaque signe écrit par le moteur (« , » « . ») sans la commande en toutes lettres rend le segment
  « à vérifier », et leur nombre est noté dans `ponctuation_moteur` (historique/etat/<code>.json).
  Un mot écrit avec une majuscule en milieu de phrase (nom propre possible) est aussi « à vérifier » :
  ajoutez les éponymes légitimes au lexique pour ne plus les voir signalés.
- **Whisper sans vocabulaire** : son invite ne contient que la modalité, pour qu'il confirme les
  corrections du compte rendu de façon indépendante.
- **Formats de dictaphone** : DSS est souvent lisible ; DS2 (Philips) ne l'est généralement pas :
  convertissez-le d'abord avec le logiciel du dictaphone.
- Une dictée en erreur n'arrête pas le lot ; cochez « Retraiter aussi les dictées en erreur » pour
  la relancer.

## Choisir les voix réservées au test

Avant le début de la collecte, désignez 1 ou 2 radiologues dans `TEST_LOCUTEURS`. Ils ne liront
que les phrases réservées au test et ne seront jamais en entraînement : leur score
(`test_voix_nouvelles`) prédit la performance chez un radiologue qui n'a jamais participé.
Ne modifiez plus cette liste une fois la collecte commencée.

## Valider et récupérer les données (machine GPU)

```bash
pip install "nemo_toolkit[asr]" jiwer boto3 soundfile
export R2_ACCOUNT_ID=... R2_ACCESS_KEY_ID=... R2_SECRET_ACCESS_KEY=... R2_BUCKET=radia-collecte

python scripts/valider_enregistrements.py --seuil 0.25        # puis onglet « À vérifier »
# ... création d'un instantané dans l'application ...
python scripts/telecharger_donnees.py /data/radia --fractions 0.25,0.5
```

`/data/radia/<instantane>/` contient `train.json`, `val.json`, les trois jeux de test,
`train_25.json` et `train_50.json` (emboîtés) et `resume.json`.

**Courbe d'apprentissage** : entraînez sur `train_25`, `train_50` puis `train`, évaluez chaque
modèle sur les mêmes jeux de test. Tant que l'erreur baisse quand les données augmentent, la
collecte vaut la peine d'être poursuivie.

## Évaluer un modèle (machine GPU)

**Avant tout ré-entraînement**, mesurez Parakeet de base : c'est le point de départ de tout gain.

```bash
python scripts/evaluer.py /data/radia/<instantane>                                   # modèle de base
python scripts/evaluer.py /data/radia/<instantane> \
    --modele nvidia/parakeet-tdt-0.6b-v3 --modele /modeles/radia_train50.nemo           # comparaison
```

Pour chaque jeu de test, le script affiche :

| Mesure | Ce qu'elle dit |
| --- | --- |
| WER hors commandes | Erreurs de mots, commandes dictées exclues. **La comparaison équitable avec le modèle de base**, qui écrit « , » au lieu de « virgule » |
| WER | Erreurs de mots, commandes comprises : ce que le formatage vocal de RadIA recevra |
| Commandes (rappel, précision) | Commandes dictées retrouvées ; commandes écrites à tort |
| Négation, latéralité, nombres | Énoncés où l'une d'elles diffère de la référence. **Les erreurs qui comptent cliniquement** |
| Termes du lexique | Taux de termes retrouvés par catégorie, et les termes les plus manqués |

Entre crochets : l'intervalle de confiance à 95 %, calculé par voix (lectures) ou par dictée (dictées
réelles). Pour un second modèle, la ligne « écart avec la référence » donne la différence de WER avec
le premier : **si son intervalle ne contient pas 0, l'amélioration est réelle**. Un jeu de moins de
300 énoncés ou de 5 voix est signalé : ses intervalles sont trop larges pour conclure.

Le rapport complet (`<instantane>/evaluation_<horodatage>.json`) contient la transcription de chaque
énoncé et ses erreurs critiques : écoutez-les avant de mettre un modèle en service.

## Construire le lexique à partir des erreurs

Le lexique le plus utile n'est pas écrit à la main : ce sont les mots que Parakeet de base manque
réellement. Après une validation (`valider_enregistrements.py`) ou un étiquetage de dictées réelles :

```bash
python scripts/construire_lexique.py              # candidats_lexique.csv + bilan affiché
python scripts/construire_lexique.py --ajouter    # ajoute aussi les candidats à lexique.csv
```

- Seuls les textes fiables sont utilisés (validés automatiquement ou relus) ; mots outils, nombres et
  commandes sont ignorés. Un mot est proposé s'il est manqué au moins 2 fois et dans au moins 30 % de
  ses occurrences (`--min-erreurs`, `--min-taux`).
- Catégorie proposée : `paire_dangereuse` quand Parakeet écrit un autre mot réel et proche
  (hypoéchogène ↔ hyperéchogène), sinon `terme_rare`.
- Le bilan donne aussi le taux d'erreur de chaque terme du lexique actuel : un terme toujours
  retrouvé n'a plus besoin d'être ciblé.
- `--ajouter` n'ajoute jamais un mot avec une majuscule (nom de personne possible) : vérifiez-le et
  ajoutez-le à la main s'il s'agit d'un éponyme. Relisez toujours la liste avant de la commiter.
- Ensuite : ajoutez des phrases qui contiennent les nouveaux termes (`verifier_phrases.py` signale
  les termes sans phrase). La sélection intelligente de l'application les proposera en priorité.

## Banque de textes

- Éditez `phrases.csv` et `lexique.csv`, puis `git push` : GitHub Actions refuse l'envoi si un
  texte viole la convention (unité abrégée, « x », ponctuation, date, civilité...).
- Vérification locale : `python scripts/verifier_phrases.py`.
- **Crochet avant envoi** (une fois par ordinateur, à la racine du dépôt) : `git config core.hooksPath .githooks`.
  Chaque `git push` vérifie alors la banque et lance les tests (Python 3.10 ou plus avec les
  dépendances ; sinon seule la banque est vérifiée). Un échec bloque l'envoi ; `git push --no-verify`
  passe outre.
- **Contrôle dans l'application** : l'onglet Administration affiche les erreurs de convention de la
  banque, et les textes en erreur ne sont plus proposés à la lecture. Cela couvre aussi les
  modifications faites directement sur le site GitHub, que le crochet ne voit pas.
- **Ne réutilisez jamais un identifiant** : la répartition entre entraînement et test en dépend.
- Un terme du lexique absent de toute phrase est signalé : ajoutez des phrases qui le contiennent.
- La liste des commandes (`COMMANDES` dans `collecte/texte.py`) doit rester identique à celle du
  formatage vocal de RadIA.

## Capture audio : à vérifier une fois

L'application enregistre avec les réglages par défaut du navigateur (réduction de bruit,
contrôle automatique du gain, annulation d'écho activés sous Chrome). Vérifiez que RadIA capte le
son **avec les mêmes réglages** : sinon le modèle s'entraînerait sur un son qu'il ne recevra pas
en production. Le champ `capture` du journal trace ce point.

## Essais locaux et tests

```bash
pip install -r requirements.txt pytest jiwer
cp .streamlit/secrets.toml.exemple .streamlit/secrets.toml   # MODE_ACCES="mot_de_passe", STOCKAGE="local", APP_PASSWORD
streamlit run app.py
pytest -q
```

En mode mot de passe, tout utilisateur est administrateur : ne l'utilisez jamais en ligne.

## Confidentialité

- Les voix sont des données de santé identifiantes, traitées par Streamlit (Snowflake) et stockées
  chez Cloudflare, **hors du Sénégal**. Le consentement affiché le dit explicitement. Ce transfert
  doit être validé par la CDP **avant** la collecte.
- Les fichiers ne portent qu'un code pseudonyme ; la correspondance e-mail ↔ code n'existe que
  dans les Secrets.
- Retrait du consentement : onglet « Retrait de consentement » (supprime enregistrements, journal,
  consentement, profil et validations), retirer le compte des Secrets, créer un nouvel instantané.
  Les modèles déjà entraînés ne peuvent pas être modifiés rétroactivement ; le consentement le précise.
