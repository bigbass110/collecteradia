"""
Prépare des données de français général et les mélange à l'entraînement d'un instantané.

À lancer sur la machine d'entraînement, APRÈS telecharger_donnees.py (ffmpeg requis) :
    # Common Voice (français) : dossier contenant clips/ et train.tsv (ou validated.tsv)
    python scripts/preparer_general.py /data/radia/<instantane> --format cv --source ~/cv-corpus/fr
    # Multilingual LibriSpeech (français) : dossier contenant transcripts.txt et audio/
    python scripts/preparer_general.py /data/radia/<instantane> --format mls --source ~/mls_french/train
    # Tout autre corpus déjà au format manifeste NeMo (audio_filepath, text, et speaker ou code)
    python scripts/preparer_general.py /data/radia/<instantane> --format manifeste --source corpus.json

Produit dans le dossier de l'instantané :
    general/audio/*.wav        audio mono 16 kHz
    general_test.json          jeu de régression (300 énoncés, voix jamais entraînées)
    general_train.json         réserve d'entraînement, dans l'ordre de sélection
    train_mixte.json           train.json + français général (25 % du total par défaut)
    train_25_mixte.json ...    idem pour chaque train_<fraction>.json (mélanges emboîtés)

Ensuite : entraîner sur train_mixte.json (voir docs/fiche-reentrainement.md), et vérifier l'absence
de régression avec : python scripts/evaluer.py <instantane> --ensembles general_test --modele ... --modele ...

Relancer le script reprend là où il s'était arrêté : l'audio déjà converti n'est pas refait.
Licences : Common Voice est sous CC0 ; MLS sous CC BY 4.0 (citer la source dans le registre des modèles).
"""
import argparse
import csv
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))
from collecte import audio as A  # noqa: E402
from collecte import general as G  # noqa: E402

DUREE_MIN, DUREE_MAX = 1.0, 20.0          # comme les phrases lues, et max_duration de la fiche
EXT_MLS = (".flac", ".opus", ".wav")


# ─────────────────────────── Lecture des corpus ───────────────────────────

def lire_cv(source: Path) -> list:
    tsv = next((source / n for n in ("train.tsv", "validated.tsv") if (source / n).exists()), None)
    if tsv is None:
        sys.exit(f"Ni train.tsv ni validated.tsv dans {source}.")
    csv.field_size_limit(10 ** 7)
    with open(tsv, encoding="utf-8", newline="") as f:
        return [{"id": l["path"], "voix": l["client_id"], "texte": l["sentence"],
                 "audio": str(source / "clips" / l["path"]), "source": "commonvoice"}
                for l in csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE)]


def lire_mls(source: Path) -> list:
    sortie = []
    for ligne in (source / "transcripts.txt").read_text(encoding="utf-8").splitlines():
        if "\t" not in ligne:
            continue
        ident, texte = ligne.split("\t", 1)
        voix, livre = ident.split("_")[:2]
        base = source / "audio" / voix / livre / ident
        audio = next((str(base) + e for e in EXT_MLS if Path(str(base) + e).exists()), str(base) + ".flac")
        sortie.append({"id": ident, "voix": voix, "texte": texte, "audio": audio, "source": "mls"})
    return sortie


def lire_manifeste(source: Path) -> list:
    sortie = []
    for k, ligne in enumerate(source.read_text(encoding="utf-8").splitlines()):
        if ligne.strip():
            l = json.loads(ligne)
            sortie.append({"id": l.get("id", str(k)), "voix": str(l.get("speaker", l.get("code", "inconnu"))),
                           "texte": l["text"], "audio": l["audio_filepath"], "source": source.stem})
    return sortie


LECTEURS = {"cv": lire_cv, "mls": lire_mls, "manifeste": lire_manifeste}


# ─────────────────────────── Conversion ───────────────────────────

def ligne_manifeste(e: dict, dossier_audio: Path):
    """Convertit l'audio d'un énoncé en WAV 16 kHz (sauf s'il l'est déjà). Renvoie la ligne du
    manifeste, ou None si l'audio est illisible ou hors des durées admises."""
    nom = hashlib.sha1(f"{e['source']}|{e['id']}".encode()).hexdigest()[:16] + ".wav"
    cible = dossier_audio / nom
    try:
        if cible.exists():
            import soundfile as sf
            duree = sf.info(str(cible)).duration
        else:
            audio = A.convertir_16k(Path(e["audio"]).read_bytes())
            duree = len(audio) / A.FREQ
            if DUREE_MIN <= duree <= DUREE_MAX:
                cible.write_bytes(A.en_wav(audio))
    except Exception:
        return None
    if not DUREE_MIN <= duree <= DUREE_MAX:
        return None
    return {"audio_filepath": str(cible.resolve()), "duration": round(duree, 3), "text": e["texte"],
            "code": "gen_" + hashlib.sha1(e["voix"].encode()).hexdigest()[:10], "id_phrase": e["id"],
            "type": "general", "source": e["source"]}


def convertir_jusqua(enonces: list, dossier_audio: Path, secondes=None, nombre=None) -> list:
    """Convertit dans l'ordre jusqu'à atteindre une durée ou un nombre d'énoncés."""
    sortie, total = [], 0.0
    for k, e in enumerate(enonces):
        if (secondes is not None and total >= secondes) or (nombre is not None and len(sortie) >= nombre):
            break
        l = ligne_manifeste(e, dossier_audio)
        if l:
            sortie.append(l)
            total += l["duration"]
        if k and k % 500 == 0:
            print(f"  {k} énoncés lus, {total / 3600:.2f} h retenues")
    return sortie


def lire_jsonl(chemin: Path) -> list:
    return [json.loads(l) for l in chemin.read_text(encoding="utf-8").splitlines() if l.strip()]


def ecrire_jsonl(chemin: Path, lignes: list) -> None:
    with open(chemin, "w", encoding="utf-8") as f:
        for l in lignes:
            f.write(json.dumps(l, ensure_ascii=False) + "\n")


# ─────────────────────────── Programme ───────────────────────────

def preparer(instantane: Path, enonces: list, proportion: float = 0.25, n_test: int = 300,
             max_par_voix: int = 50, graine: str = "radia") -> dict:
    train = lire_jsonl(instantane / "train.json") if (instantane / "train.json").exists() else []
    if not train:
        sys.exit(f"train.json absent ou vide dans {instantane} : lancez d'abord telecharger_donnees.py.")
    rejets, retenus = Counter(), []
    for e in enonces:
        texte, motif = G.convertir(e["texte"])
        if texte:
            retenus.append(dict(e, texte=texte))
        else:
            rejets[motif] += 1
    test_brut, reserve = G.repartir(retenus, n_test, max_par_voix, graine)

    dossier_audio = instantane / "general" / "audio"
    dossier_audio.mkdir(parents=True, exist_ok=True)
    print(f"Jeu de régression ({len({e['voix'] for e in test_brut})} voix)...")
    test = convertir_jusqua(test_brut, dossier_audio, nombre=n_test)
    besoin = G.secondes_a_ajouter(sum(float(l["duration"]) for l in train), proportion)
    print(f"Entraînement : {besoin / 3600:.2f} h de français général à préparer...")
    general = convertir_jusqua(reserve, dossier_audio, secondes=besoin)

    ecrire_jsonl(instantane / "general_test.json", test)
    ecrire_jsonl(instantane / "general_train.json", general)
    mixtes = {}
    for chemin in sorted(instantane.glob("train*.json")):
        if chemin.stem.endswith("_mixte"):
            continue
        lignes = G.melanger(lire_jsonl(chemin), general, proportion)
        ecrire_jsonl(instantane / f"{chemin.stem}_mixte.json", lignes)
        mixtes[f"{chemin.stem}_mixte"] = len(lignes)
    heures = sum(l["duration"] for l in general) / 3600
    bilan = {"enonces_lus": len(enonces), "rejets": dict(rejets), "retenus": len(retenus),
             "general_test": len(test), "general_train": len(general), "heures_general": round(heures, 2),
             "heures_voulues": round(besoin / 3600, 2), "voix_train": len({l["code"] for l in general}),
             "proportion": proportion, "mixtes": mixtes}
    (instantane / "general_resume.json").write_text(json.dumps(bilan, ensure_ascii=False, indent=1), encoding="utf-8")
    return bilan


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("instantane", type=Path, help="dossier produit par telecharger_donnees.py")
    p.add_argument("--format", choices=sorted(LECTEURS), required=True)
    p.add_argument("--source", type=Path, required=True, help="dossier du corpus (cv, mls) ou fichier manifeste")
    p.add_argument("--proportion", type=float, default=0.25, help="part du français général dans le mélange (0,2 à 0,3)")
    p.add_argument("--test", type=int, default=300, help="énoncés du jeu de régression general_test")
    p.add_argument("--max-par-voix", type=int, default=50)
    p.add_argument("--graine", default="radia")
    a = p.parse_args(argv)

    enonces = LECTEURS[a.format](a.source.expanduser())
    print(f"{len(enonces)} énoncé(s) dans le corpus.")
    b = preparer(a.instantane, enonces, a.proportion, a.test, a.max_par_voix, a.graine)
    print(f"\nRetenus après conversion du texte : {b['retenus']} ; rejets : "
          + ", ".join(f"{m} {n}" for m, n in sorted(b["rejets"].items(), key=lambda x: -x[1])))
    print(f"general_test : {b['general_test']} énoncé(s) ; general_train : {b['general_train']} énoncé(s), "
          f"{b['heures_general']} h sur {b['heures_voulues']} h voulues, {b['voix_train']} voix.")
    if b["heures_general"] < 0.9 * b["heures_voulues"]:
        print("⚠ Moins de français général que voulu : corpus trop petit, ou --max-par-voix trop bas.")
    if b["general_test"] < a.test:
        print("⚠ Jeu de régression incomplet : corpus trop petit.")
    for nom, n in b["mixtes"].items():
        print(f"  {nom}.json : {n} ligne(s)")
    print("\nEntraînez sur train_mixte.json (docs/fiche-reentrainement.md), puis vérifiez la régression :\n"
          f"  python scripts/evaluer.py {a.instantane} --ensembles general_test --modele <base> --modele <nouveau.nemo>")


if __name__ == "__main__":
    main()
