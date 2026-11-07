"""
Validation automatique des enregistrements avec Parakeet de BASE (non ré-entraîné).

Pour chaque enregistrement pas encore validé : transcription, puis comparaison avec le texte
attendu (sans tenir compte de la ponctuation ni des commandes dictées). Un enregistrement est
marqué « a_verifier » si l'écart (CER) dépasse le seuil, ou si une négation, une latéralité ou un
nombre diffère. Le coordinateur tranche ensuite dans l'onglet « À vérifier » de l'application.

À lancer sur une machine avec GPU NVIDIA (jamais dans Streamlit Cloud) :
    pip install "nemo_toolkit[asr]" jiwer boto3 soundfile
    export R2_ACCOUNT_ID=... R2_ACCESS_KEY_ID=... R2_SECRET_ACCESS_KEY=... R2_BUCKET=radia-collecte
    python scripts/valider_enregistrements.py --seuil 0.25

Ce script ÉCRIT validations/resultats.json : utilisez un jeton R2 en lecture et écriture.
Sans variables R2_*, il travaille sur le dossier local ./donnees (essais).
"""
import argparse
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))
from collecte import evaluation as EV  # noqa: E402
from collecte import jeux as J  # noqa: E402
from collecte import texte as T  # noqa: E402
from collecte import travail as TR  # noqa: E402
from collecte.stockage import depuis_environnement, lire_json  # noqa: E402

NEGATIONS, LATERALITES = EV.NEGATIONS, EV.LATERALITES   # source unique : collecte/evaluation.py


def comparer(attendu: str, hypothese: str, seuil: float) -> dict:
    import jiwer
    a = T.sans_commandes(T.normaliser(attendu))
    h = T.sans_commandes(T.normaliser(hypothese))
    cer = float(jiwer.cer(a, h)) if a else 1.0
    raisons = []
    if {m for m in a.split() if m in NEGATIONS} != {m for m in h.split() if m in NEGATIONS}:
        raisons.append("négation")
    if {m for m in a.split() if m in LATERALITES} != {m for m in h.split() if m in LATERALITES}:
        raisons.append("latéralité")
    if re.findall(r"\d+(?:,\d+)?", a) != re.findall(r"\d+(?:,\d+)?", h):
        raisons.append("nombres")
    if cer > seuil:
        raisons.append(f"CER > {seuil:.0%}")
    return {"cer": round(cer, 4), "raisons": raisons, "statut": "a_verifier" if raisons else "ok"}


def transcripteur_parakeet(nom_modele: str):
    import nemo.collections.asr as nemo_asr
    modele = nemo_asr.models.ASRModel.from_pretrained(nom_modele)

    def transcrire(chemins: list) -> list:
        sorties = modele.transcribe(chemins, batch_size=8)
        return [s.text if hasattr(s, "text") else s for s in sorties]
    return transcrire


def valider(stockage, transcrire, nom_modele: str, seuil: float, tout: bool = False, lot: int = 32) -> dict:
    journal = []
    for cle in stockage.lister("journaux/"):
        if cle.endswith(".json"):
            journal += lire_json(stockage, cle, [])
    if TR.travail_en_cours(stockage):
        sys.exit("Un étiquetage GPU est en cours : relancez la validation quand il sera terminé "
                 "(Administration → « Dictées réelles »).")
    if J.validation_en_cours(stockage):
        sys.exit(f"Une autre validation semble en cours. Si ce n'est pas le cas (programme interrompu), "
                 f"supprimez {J.CLE_VALIDATION_EN_COURS} ou attendez 20 minutes.")
    resultats = lire_json(stockage, J.CLE_RESULTATS, {})
    a_traiter = [j for j in journal if tout or j["fichier"] not in resultats]
    print(f"{len(a_traiter)} enregistrement(s) à valider sur {len(journal)}.")
    J.marquer_validation(stockage, True)
    try:
        _valider_par_lots(stockage, transcrire, nom_modele, seuil, a_traiter, lot)
    finally:
        J.marquer_validation(stockage, False)
    resultats = lire_json(stockage, J.CLE_RESULTATS, {})
    a_verifier = sum(1 for r in resultats.values() if r["statut"] == "a_verifier")
    print(f"Terminé : {a_verifier} enregistrement(s) à vérifier dans l'application.")
    return resultats


def _valider_par_lots(stockage, transcrire, nom_modele: str, seuil: float, a_traiter: list, lot: int) -> None:
    for i in range(0, len(a_traiter), lot):
        paquet = a_traiter[i:i + lot]
        with tempfile.TemporaryDirectory() as d:
            chemins = []
            for j in paquet:
                chemin = Path(d) / Path(j["fichier"]).name
                chemin.write_bytes(stockage.lire(j["fichier"]) or b"")
                chemins.append(str(chemin))
            hypotheses = transcrire(chemins)
        nouveaux = {}
        for j, h in zip(paquet, hypotheses):
            r = comparer(j["texte_final"], h, seuil)
            nouveaux[j["fichier"]] = r | {"hypothese": h, "modele": nom_modele,
                                          "horodatage": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        J.maj_resultats(stockage, nouveaux)                 # fusion : ne touche que ce lot
        J.marquer_validation(stockage, True)                # le marqueur reste frais pendant un long travail
        print(f"  {min(i + lot, len(a_traiter))}/{len(a_traiter)}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--modele", default="nvidia/parakeet-tdt-0.6b-v3")
    p.add_argument("--seuil", type=float, default=0.25, help="CER au-delà duquel on demande une vérification")
    p.add_argument("--tout", action="store_true", help="revalider aussi les enregistrements déjà validés")
    args = p.parse_args()
    valider(depuis_environnement(), transcripteur_parakeet(args.modele), args.modele, args.seuil, args.tout)
