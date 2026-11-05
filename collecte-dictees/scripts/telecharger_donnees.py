"""
Télécharge un instantané (manifestes + audio) vers un dossier local, prêt pour NeMo.

Usage (machine d'entraînement, jeton R2 en LECTURE SEULE) :
    pip install boto3
    export R2_ACCOUNT_ID=... R2_ACCESS_KEY_ID=... R2_SECRET_ACCESS_KEY=... R2_BUCKET=radia-collecte
    python scripts/telecharger_donnees.py /chemin/donnees                      # dernier instantané
    python scripts/telecharger_donnees.py /chemin/donnees --instantane 2026-11-05_101500
    python scripts/telecharger_donnees.py /chemin/donnees --fractions 0.25,0.5 # courbe d'apprentissage

Produit /chemin/donnees/<instantane>/ avec les manifestes en chemins absolus, le résumé,
et, avec --fractions, des sous-ensembles emboîtés de train (train_25.json ⊂ train_50.json ⊂ train.json).
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))
from collecte.jeux import ENSEMBLES  # noqa: E402
from collecte.stockage import depuis_environnement, lire_json  # noqa: E402


def telecharger(stockage, dest: Path, instantane: str | None, fractions: list) -> Path:
    if not instantane:
        instantane = lire_json(stockage, "manifestes/dernier.json", {}).get("instantane")
        if not instantane:
            sys.exit("Aucun instantané : créez-en un dans l'onglet « Jeux de données » de l'application.")
    sortie = dest / instantane
    sortie.mkdir(parents=True, exist_ok=True)
    resume = lire_json(stockage, f"manifestes/{instantane}/resume.json", {})
    (sortie / "resume.json").write_text(json.dumps(resume, ensure_ascii=False, indent=1), encoding="utf-8")

    n = 0
    for ens in ENSEMBLES:
        brut = stockage.lire(f"manifestes/{instantane}/{ens}.json")
        lignes = [json.loads(l) for l in (brut or b"").decode("utf-8").splitlines() if l.strip()]
        for l in lignes:
            cible = dest / "audio" / l["audio_filepath"]
            if not cible.exists():
                cible.parent.mkdir(parents=True, exist_ok=True)
                cible.write_bytes(stockage.lire(l["audio_filepath"]) or b"")
                n += 1
            l["audio_filepath"] = str(cible.resolve())
        ecrire(sortie / f"{ens}.json", lignes)
        print(f"{ens} : {len(lignes)} ligne(s)")
        if ens == "train" and fractions:
            # Ordre stable par voix et phrase : les sous-ensembles sont emboîtés
            ordre = sorted(lignes, key=lambda l: hashlib.sha1(f"{l['code']}|{l['id_phrase']}".encode()).hexdigest())
            for f in fractions:
                k = round(len(ordre) * f)
                ecrire(sortie / f"train_{round(f * 100)}.json", ordre[:k])
                print(f"  train_{round(f * 100)} : {k} ligne(s)")
    print(f"{n} fichier(s) audio téléchargé(s). Manifestes dans {sortie}")
    return sortie


def ecrire(chemin: Path, lignes: list) -> None:
    with open(chemin, "w", encoding="utf-8") as f:
        for l in lignes:
            f.write(json.dumps(l, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("dest", type=Path)
    p.add_argument("--instantane")
    p.add_argument("--fractions", default="", help="ex. 0.25,0.5")
    a = p.parse_args()
    fr = [float(x) for x in a.fractions.split(",") if x.strip()]
    telecharger(depuis_environnement(), a.dest.resolve(), a.instantane, fr)
