"""
Évalue un ou plusieurs modèles sur les jeux de test d'un instantané (téléchargé par telecharger_donnees.py).

Usage (machine GPU) :
    pip install "nemo_toolkit[asr]"
    python scripts/evaluer.py /data/radia/2026-11-05_101500                          # Parakeet de base
    python scripts/evaluer.py /data/radia/2026-11-05_101500 \
        --modele nvidia/parakeet-tdt-0.6b-v3 --modele /modeles/radia_train50.nemo     # comparaison

Le PREMIER modèle sert de référence : pour les suivants, l'écart de WER est donné avec son intervalle
de confiance apparié (mêmes énoncés, mêmes tirages). Si l'intervalle ne contient pas 0, l'écart est réel.

Mesures (voir collecte/evaluation.py) : WER, WER hors commandes (comparaison équitable avec le modèle
de base, qui écrit des signes au lieu des commandes), rappel des commandes, erreurs critiques
(négation, latéralité, nombres), rappel des termes du lexique par catégorie.

Le rapport complet (mesures + transcription de chaque énoncé) est écrit dans
<instantane>/evaluation_<horodatage>.json : utile pour écouter les erreurs critiques.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))
from collecte import evaluation as EV  # noqa: E402
from collecte import texte as T  # noqa: E402

ENSEMBLES_TEST = ["test_voix_connues", "test_voix_nouvelles", "test_comptes_rendus", "test_historique"]
MODELE_BASE = "nvidia/parakeet-tdt-0.6b-v3"
ENONCES_CONSEILLES = 300
LONG_S = 30.0          # au-delà, transcription une par une (comptes rendus complets)


# ─────────────────────────── Lecture et transcription ───────────────────────────

def lire_manifestes(dossier: Path, ensembles: list) -> dict:
    sortie = {}
    for e in ensembles:
        chemin = dossier / f"{e}.json"
        if chemin.exists():
            lignes = [json.loads(l) for l in chemin.read_text(encoding="utf-8").splitlines() if l.strip()]
            if lignes:
                sortie[e] = lignes
    return sortie


def transcripteur_nemo(nom: str):
    """Fonction(chemins) -> textes. Un chemin .nemo est un modèle local (ré-entraîné)."""
    import nemo.collections.asr as nemo_asr
    if nom.endswith(".nemo"):
        modele = nemo_asr.models.ASRModel.restore_from(nom)
    else:
        modele = nemo_asr.models.ASRModel.from_pretrained(nom)
    modele.eval()

    def transcrire(chemins: list, taille_lot: int) -> list:
        sorties = modele.transcribe(chemins, batch_size=taille_lot)
        return [s.text if hasattr(s, "text") else s for s in sorties]
    return transcrire


def transcrire_tout(manifestes: dict, transcrire, taille_lot: int = 16) -> dict:
    """Transcrit chaque fichier audio une seule fois. Renvoie {audio_filepath: hypothèse}."""
    durees = {}
    for lignes in manifestes.values():
        for l in lignes:
            durees[l["audio_filepath"]] = float(l.get("duration", 0))
    courts = sorted((c for c, d in durees.items() if d <= LONG_S), key=durees.get)
    longs = sorted((c for c, d in durees.items() if d > LONG_S), key=durees.get)
    hypotheses = {}
    for i in range(0, len(courts), taille_lot):
        paquet = courts[i:i + taille_lot]
        hypotheses.update(zip(paquet, transcrire(paquet, taille_lot)))
    for c in longs:
        hypotheses[c] = transcrire([c], 1)[0]
    return hypotheses


# ─────────────────────────── Évaluation ───────────────────────────

def evaluer(manifestes: dict, hypotheses: dict, lexique: list) -> dict:
    """hypotheses : {nom du modèle: {audio_filepath: texte}}, dans l'ordre (le premier = référence).
    Calcul pur, sans GPU : c'est cette fonction qui est testée."""
    modeles = list(hypotheses)
    rapport = {"modeles": modeles, "ensembles": {}}
    for ens, lignes in manifestes.items():
        groupes = [EV.groupe_de(l) for l in lignes]
        par_modele = {m: [EV.comparer(l["text"], hypotheses[m].get(l["audio_filepath"], ""), lexique)
                          for l in lignes] for m in modeles}
        bloc = {"enonces": len(lignes), "groupes": len(set(groupes)),
                "heures": round(sum(float(l.get("duration", 0)) for l in lignes) / 3600, 3),
                "mesures": {}, "detail": []}
        for m in modeles:
            mesures = EV.agreger(par_modele[m], lexique)
            mesures["wer_ic"] = EV.intervalle(par_modele[m], groupes, "erreurs", "mots")
            mesures["wer_hors_commandes_ic"] = EV.intervalle(par_modele[m], groupes)
            if m != modeles[0]:
                mesures["ecart_wer_hors_commandes"] = (mesures["wer_hors_commandes"] or 0) - (
                    bloc["mesures"][modeles[0]]["wer_hors_commandes"] or 0)
                mesures["ecart_wer_hors_commandes_ic"] = EV.intervalle(
                    par_modele[m], groupes, ecart_avec=par_modele[modeles[0]])
            bloc["mesures"][m] = mesures
        for k, l in enumerate(lignes):
            bloc["detail"].append({
                "audio_filepath": l["audio_filepath"], "reference": l["text"], "groupe": groupes[k],
                "hypotheses": {m: hypotheses[m].get(l["audio_filepath"], "") for m in modeles},
                "critiques": {m: [c for c, e in par_modele[m][k]["critiques"].items() if e] for m in modeles},
            })
        rapport["ensembles"][ens] = bloc
    return rapport


# ─────────────────────────── Affichage ───────────────────────────

def _pct(x) -> str:
    return "—" if x is None else f"{100 * x:.1f} %".replace(".", ",")


def _ic(ic) -> str:
    return "" if not ic else f" [{100 * ic[0]:.1f} ; {100 * ic[1]:.1f}]".replace(".", ",")


def tableau(rapport: dict) -> str:
    modeles = rapport["modeles"]
    noms = [Path(m).name if m.endswith(".nemo") else m.split("/")[-1] for m in modeles]
    largeur = max(30, *(len(n) + 2 for n in noms))
    sortie = []

    def ligne(titre, valeurs):
        sortie.append(f"  {titre:<38}" + "".join(f"{v:<{largeur}}" for v in valeurs))

    for ens, bloc in rapport["ensembles"].items():
        sortie.append(f"\n== {ens} : {bloc['enonces']} énoncés, {bloc['groupes']} groupes, "
                      f"{bloc['heures']:.2f} h".replace(".", ","))
        if bloc["enonces"] < ENONCES_CONSEILLES or bloc["groupes"] < 5:
            sortie.append(f"   ⚠ jeu petit (conseillé : {ENONCES_CONSEILLES} énoncés et 5 groupes ou plus) : "
                          "intervalles larges, conclure avec prudence")
        me = [bloc["mesures"][m] for m in modeles]
        ligne("", noms)
        ligne("WER hors commandes", [_pct(x["wer_hors_commandes"]) + _ic(x["wer_hors_commandes_ic"]) for x in me])
        if len(modeles) > 1:
            ligne("  écart avec la référence", ["référence"] + [
                f"{100 * x['ecart_wer_hors_commandes']:+.1f} pts".replace(".", ",")
                + _ic(x["ecart_wer_hors_commandes_ic"]) for x in me[1:]])
        ligne("WER (commandes comprises)", [_pct(x["wer"]) + _ic(x["wer_ic"]) for x in me])
        ligne("Commandes : rappel", [_pct(x["commandes_rappel"]) for x in me])
        ligne("Commandes : précision", [_pct(x["commandes_precision"]) for x in me])
        for c, titre in (("negation", "Négation fausse"), ("lateralite", "Latéralité fausse"),
                         ("nombres", "Nombres faux")):
            ligne(titre, [f"{x['critiques'][c]['erreurs']}/{x['critiques'][c]['concernes']} "
                          f"({_pct(x['critiques'][c]['taux'])})" if c in x["critiques"] else "—" for x in me])
        for cat in sorted({c for x in me for c in x["termes"]}):
            ligne(f"Termes « {cat} » : rappel", [
                f"{_pct(x['termes'][cat]['rappel'])} ({x['termes'][cat]['retrouves']}/{x['termes'][cat]['attendus']})"
                if cat in x["termes"] else "—" for x in me])
        manques = sorted(((t, v["attendus"] - v["retrouves"], v["attendus"])
                          for t, v in me[-1]["par_terme"].items() if v["retrouves"] < v["attendus"]),
                         key=lambda t: -t[1])[:8]
        if manques:
            sortie.append(f"  Termes les plus manqués ({noms[-1]}) : "
                          + ", ".join(f"{t} ({n}/{a})" for t, n, a in manques))
    return "\n".join(sortie)


# ─────────────────────────── Programme ───────────────────────────

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("instantane", type=Path, help="dossier produit par telecharger_donnees.py")
    p.add_argument("--modele", action="append",
                   help=f"nom NeMo ou chemin .nemo ; répétable, le premier sert de référence (défaut : {MODELE_BASE})")
    p.add_argument("--ensembles", default=",".join(ENSEMBLES_TEST))
    p.add_argument("--lexique", type=Path, default=RACINE / "lexique.csv")
    p.add_argument("--lot", type=int, default=16, help="taille des lots de transcription")
    p.add_argument("--sortie", type=Path, help="rapport JSON (défaut : <instantane>/evaluation_<horodatage>.json)")
    a = p.parse_args(argv)

    manifestes = lire_manifestes(a.instantane, [e.strip() for e in a.ensembles.split(",") if e.strip()])
    if not manifestes:
        sys.exit(f"Aucun jeu de test non vide dans {a.instantane}.")
    lexique = T.lire_lexique(a.lexique.read_bytes())
    hypotheses = {}
    for nom in a.modele or [MODELE_BASE]:
        print(f"Transcription avec {nom}...")
        transcrire = transcripteur_nemo(nom)
        hypotheses[nom] = transcrire_tout(manifestes, transcrire, a.lot)
        del transcrire                                  # libère le GPU avant le modèle suivant
        try:
            import torch
            torch.cuda.empty_cache()
        except Exception:
            pass

    rapport = evaluer(manifestes, hypotheses, lexique)
    rapport["instantane"] = a.instantane.name
    rapport["cree_le"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    sortie = a.sortie or a.instantane / f"evaluation_{datetime.now(timezone.utc):%Y-%m-%d_%H%M%S}.json"
    sortie.write_text(json.dumps(rapport, ensure_ascii=False, indent=1), encoding="utf-8")
    print(tableau(rapport))
    print(f"\nRapport complet : {sortie}")


if __name__ == "__main__":
    main()
