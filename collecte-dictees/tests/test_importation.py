import io
import shutil
import zipfile
from types import SimpleNamespace

import numpy as np
import pytest

from collecte import audio as A
from collecte import importation as I
from collecte import jeux as J
from collecte import travail as TR
from collecte.stockage import StockageLocal, ecrire_json, lire_json

CR = "Nom : DIOP Amadou\nMammographie bilatérale.\nPas d'adénopathie axillaire. Classé BI-RADS 4."


def fichier(nom, donnees=b"x"):
    return SimpleNamespace(name=nom, getvalue=lambda: donnees)


def test_deballer_zip():
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w") as z:
        z.writestr("lot/0001.m4a", b"audio")
        z.writestr("lot/0001.docx", b"cr")
        z.writestr("__MACOSX/lot/._0001.m4a", b"x")
        z.writestr("lot/.DS_Store", b"x")
        z.writestr("lot/sous/", b"")
    fichiers, pbs = I.deballer([fichier("dictees.zip", tampon.getvalue()), fichier("0002.mp3")])
    assert sorted(f.name for f in fichiers) == ["0001.docx", "0001.m4a", "0002.mp3"]
    assert next(f for f in fichiers if f.name == "0001.m4a").getvalue() == b"audio"
    assert pbs == []
    assert I.deballer([fichier("faux.zip", b"pas une archive")])[1] == ["faux.zip : archive .zip illisible"]


def test_apparier_detail_garde_les_fichiers_seuls():
    paires, audios, textes, pbs = I.apparier_detail(
        [fichier("0001.m4a"), fichier("0001.rtf"), fichier("DICT_17.dss"), fichier("CR Diop.docx"), fichier("x.exe")])
    assert [(a.name, t.name) for a, t in paires] == [("0001.m4a", "0001.rtf")]
    assert [a.name for a in audios] == ["DICT_17.dss"] and [t.name for t in textes] == ["CR Diop.docx"]
    assert pbs == ["x.exe : format non pris en charge"]


def test_extraction_rtf_et_odt():
    rtf = rb"{\rtf1\ansi{\fonttbl{\f0 Arial;}}\f0 Nodule spicul\'e9 du sein gauche.\par Pas d'ad\'e9nopathie.}"
    assert I.extraire_texte("cr.rtf", rtf) == "Nodule spiculé du sein gauche.\nPas d'adénopathie."
    contenu = ('<?xml version="1.0"?><office:document-content '
               'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
               'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"><office:body><office:text>'
               '<text:h>Scanner thoracique</text:h><text:p>Plage de <text:span>verre dépoli</text:span>.</text:p>'
               '</office:text></office:body></office:document-content>')
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w") as z:
        z.writestr("content.xml", contenu)
    assert I.extraire_texte("cr.odt", tampon.getvalue()) == "Scanner thoracique\nPlage de verre dépoli."


def test_extraction_doc_sans_antiword(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda nom: None)
    with pytest.raises(ValueError, match="docx"):
        I.extraire_texte("cr.doc", b"\xd0\xcf\x11\xe0")


def test_apercu_compte_rendu():
    gardees, retirees = I.apercu_compte_rendu(CR)
    assert retirees == ["Nom : DIOP Amadou"]
    assert gardees == ["Mammographie bilatérale.", "Pas d'adénopathie axillaire. Classé BI-RADS 4."]


def dictee_etiquetee(s, code="dr01"):
    """Une dictée importée et étiquetée, avec un segment relu par le coordinateur."""
    ident, _ = I.enregistrer(s, code, A.en_wav(np.full(A.FREQ * 8, 0.01, dtype="float32")), 8.0, CR, "Sénologie", "r")
    mots = lambda t: [{"mot": m, "debut": 0.4 * k, "fin": 0.4 * k + 0.3} for k, m in enumerate(t.split())]
    dictee = "pas d'adénopathie axillaire point classé BI-RADS 4 point"
    TR.traiter(s, lambda c, m: mots(dictee), lambda c, m: mots(dictee), set())
    segments = [j["fichier"] for j in lire_json(s, f"journaux/hist_{code}.json", [])]
    ecrire_json(s, J.CLE_DECISIONS, {segments[0]: {"statut": "valide_humain"}, "brut/autre.wav": {"statut": "rejete"}})
    J.maj_resultats(s, {"brut/autre.wav": {"statut": "ok"}})
    return ident, segments


def test_supprimer_une_dictee(tmp_path):
    s = StockageLocal(tmp_path)
    ident, segments = dictee_etiquetee(s)
    autre, _ = I.enregistrer(s, "dr01", A.en_wav(np.full(A.FREQ * 6, 0.02, dtype="float32")), 6.0, CR, "", "r")
    assert I.supprimer_dictee(s, "dr01", ident) == len(segments) > 0
    assert [e["id"] for e in I.index_de(s, "dr01")] == [autre]
    assert s.lire(f"historique/audio/{ident}.wav") is None and s.lire(f"historique/cr/{ident}.txt") is None
    assert all(s.lire(f) is None for f in segments)
    assert lire_json(s, "journaux/hist_dr01.json", []) == []
    assert set(lire_json(s, J.CLE_RESULTATS, {})) == {"brut/autre.wav"}          # le reste est intact
    assert set(lire_json(s, J.CLE_DECISIONS, {})) == {"brut/autre.wav"}
    assert ident not in I.etat_de(s, "dr01")


def test_modifier_modalite_garde_les_segments(tmp_path):
    s = StockageLocal(tmp_path)
    ident, segments = dictee_etiquetee(s)
    assert I.modifier_dictee(s, "dr01", ident, modalite="Thorax") == ""
    assert I.index_de(s, "dr01")[0]["modalite"] == "Thorax"
    journal = lire_json(s, "journaux/hist_dr01.json", [])
    assert len(journal) == len(segments) and {j["modalite"] for j in journal} == {"Thorax"}
    assert I.etat_de(s, "dr01")[ident]["statut"] == "etiquete"


def test_remplacer_le_compte_rendu_relance_l_etiquetage(tmp_path):
    s = StockageLocal(tmp_path)
    ident, segments = dictee_etiquetee(s)
    assert I.modifier_dictee(s, "dr01", ident, texte_cr="   ") == "compte rendu vide"
    assert I.modifier_dictee(s, "dr01", ident, texte_cr="IRM du genou droit. Ménisque normal.") == ""
    assert s.lire(f"historique/cr/{ident}.txt").decode() == "IRM du genou droit. Ménisque normal."
    assert lire_json(s, "journaux/hist_dr01.json", []) == [] and all(s.lire(f) is None for f in segments)
    assert [d["id"] for d in TR.dictees_en_attente(s, False)] == [ident]       # repasse en attente
    assert I.modifier_dictee(s, "dr01", "inconnue", modalite="Thorax") == "dictée introuvable"
