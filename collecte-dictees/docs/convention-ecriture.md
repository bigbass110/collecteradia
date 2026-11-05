# Convention d'écriture des transcriptions

Règle unique, appliquée aux phrases à lire, aux corrections et à tous les manifestes.
Elle est vérifiée automatiquement par `scripts/verifier_phrases.py` (à chaque envoi sur GitHub)
et par l'application quand un texte est corrigé.

**On écrit exactement les mots prononcés.** La mise en forme (ponctuation, « 12 x 8 mm »,
majuscules de début de phrase) est le travail du formatage vocal de RadIA, après la transcription.

| Situation | On écrit | On n'écrit pas |
| --- | --- | --- |
| Commande de ponctuation dictée | `virgule`, `point`, `point à la ligne`, `deux points` | `,` `.` `:` |
| Signe de ponctuation non dicté | rien | `.` `,` |
| Mesures | `12 fois 8 millimètres` | `12 x 8 mm`, `12x8mm` |
| Nombres | `12`, `3,5` (chiffres, virgule décimale) | `douze`, `3.5` |
| Numéros de segment, stade, grade | `segment 7`, `stade 4` | `segment VII` |
| Scores et sigles | `BI-RADS 4`, `IRM`, `T2 FLAIR`, `L4 L5` | `birads quatre` |
| Noms propres (éponymes) | `Bosniak`, `Fleischner` (à déclarer dans `lexique.csv`) | |
| Reste du texte | minuscules | Majuscule en début de phrase |
| Hésitations (« euh ») | rien | `euh` |
| Dates, civilités, noms de personnes | **interdits** | |

Les commandes reconnues sont listées dans `collecte/texte.py` (`COMMANDES`). Cette liste doit être
**identique** à celle du formatage vocal de RadIA ; toute commande ajoutée d'un côté doit l'être
de l'autre.

**Conséquence pour le modèle ré-entraîné** : il cessera d'ajouter lui-même la ponctuation et
écrira les commandes dictées en toutes lettres. C'est voulu : la ponctuation devient
déterministe et ne dépend que de ce que le radiologue a dicté.
