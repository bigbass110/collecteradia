"""
RadIA · Collecte de dictées pour le ré-entraînement de la reconnaissance vocale (Parakeet).

Déploiement : dépôt GitHub -> Streamlit Community Cloud.
Connexion   : compte Google (st.login), liste blanche d'e-mails dans les Secrets.
Stockage    : Cloudflare R2 (le disque de Streamlit Cloud est effacé à chaque redémarrage).

Organisation du stockage :
  brut/<code>_<id>.wav, .txt         audio mono 16 kHz rogné + texte exact
  journaux/<code>.json               une entrée par enregistrement (métadonnées comprises)
  consentements/<code>.json          version et texte du consentement accepté
  profils/<code>.json                dernier micro / lieu déclarés
  signalements/*.json                textes signalés
  validations/resultats.json         validation automatique (scripts/valider_enregistrements.py)
  validations/decisions.json         décisions du coordinateur
  manifestes/<horodatage>/*.json     instantanés versionnés pour NeMo
  historique/...                     dictées réelles importées (voir collecte/importation.py)
  brut/hist_<id>_sNNN.wav            segments étiquetés par le programme GPU (gpu/modal_etiquetage.py)
"""
import html
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

from collecte import VERSION_APP
from collecte import audio as A
from collecte import importation as I
from collecte import jeux as J
from collecte import travail as TR
from collecte import texte as T
from collecte.stockage import StockageLocal, StockageR2, ecrire_json, lire_json

BASE = Path(__file__).parent
VERSION_CONSENTEMENT = "v3"
FORMAT_CODE = re.compile(r"^dr\d{2}$")
MICROS = ["Micro de dictée", "Casque avec micro", "Micro intégré de l'ordinateur", "Téléphone", "Autre"]
LIEUX = ["Salle de lecture", "Bureau", "Domicile", "Autre"]

st.set_page_config(page_title="RadIA · Collecte de dictées", page_icon="🎙️", layout="centered")
st.markdown("""
<style>
.carte-phrase { border-left: 4px solid #E0A43A; background: rgba(224,164,58,0.08);
  padding: 1.4rem 1.6rem; border-radius: 6px; font-size: 1.5rem; line-height: 1.6;
  margin: 0.4rem 0 1.2rem 0; }
.carte-cr { font-size: 1.15rem; line-height: 1.75; }
.carte-meta { opacity: 0.75; font-size: 0.95rem; margin-bottom: 0.2rem; }
.cmd { color: #E0A43A; font-variant: small-caps; letter-spacing: 0.02em;
  border-bottom: 1px dotted #E0A43A; }
</style>
""", unsafe_allow_html=True)


# ─────────────────────────── Configuration ───────────────────────────

def secret(nom, defaut=None):
    try:
        return st.secrets.get(nom, defaut)
    except Exception:
        return defaut


def maintenant() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@st.cache_resource
def stockage():
    if str(secret("STOCKAGE", "local")).lower() == "r2":
        return StockageR2(dict(secret("r2")))
    return StockageLocal(BASE / "donnees")


@st.cache_data
def banque():
    contenu = (BASE / "phrases.csv").read_bytes()
    return T.lire_banque(contenu), T.version_banque(contenu)


@st.cache_data
def lexique():
    chemin = BASE / "lexique.csv"
    return T.lire_lexique(chemin.read_bytes()) if chemin.exists() else []


@st.cache_data
def problemes_banque() -> list:
    """Contrôle de phrases.csv et lexique.csv au démarrage : même correcteur que GitHub Actions."""
    return T.verifier_banque(banque()[0], lexique())


@st.cache_data
def termes_par_id() -> dict:
    return {p["id"]: sorted(T.termes_de(p["texte"], lexique())) for p in banque()[0]}


@st.cache_data(ttl=60, show_spinner=False)
def tous_les_journaux() -> list:
    lignes = []
    for cle in stockage().lister("journaux/"):
        if cle.endswith(".json"):
            lignes += lire_json(stockage(), cle, [])
    return lignes


def journal_de(code: str) -> list:
    return lire_json(stockage(), f"journaux/{code}.json", [])


def test_locuteurs() -> set:
    return {str(c).strip().lower() for c in (secret("TEST_LOCUTEURS", []) or [])}


def codes_connus() -> list:
    codes = {str(v).strip().lower() for v in dict(secret("locuteurs", {}) or {}).values()}
    codes |= {c.split("/")[-1][:-5] for c in stockage().lister("consentements/")}
    return sorted(c for c in codes if FORMAT_CODE.match(c))


def cle_journal(entree: dict) -> str:
    cle = entree.get("journal")
    return cle if isinstance(cle, str) and cle else f"journaux/{entree['code']}.json"


def infos_navigateur() -> tuple:
    try:
        ua = st.context.headers.get("User-Agent", "") or ""
    except Exception:
        ua = ""
    nav = next((n for k, n in [("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"),
                               ("Chrome/", "Chrome"), ("Safari/", "Safari")] if k in ua), "Autre")
    plat = next((n for k, n in [("Android", "Android"), ("iPhone", "iOS"), ("iPad", "iOS"),
                                ("Windows", "Windows"), ("Mac OS", "macOS"), ("Linux", "Linux")] if k in ua),
                "Autre")
    return nav, plat


# ─────────────────────────── Identité ───────────────────────────

def identifier():
    """{"code": "dr01" ou None, "admin": bool}, ou None si l'écran de connexion est affiché."""
    if str(secret("MODE_ACCES", "mot_de_passe")).lower() == "google":
        if not st.user.is_logged_in:
            st.title("Collecte de dictées RadIA")
            st.write("Connectez-vous avec le compte Google que vous avez communiqué au coordinateur.")
            st.button("Se connecter avec Google", type="primary", on_click=st.login)
            return None
        email = (st.user.to_dict().get("email") or "").strip().lower()
        table = {k.strip().lower(): str(v).strip().lower()
                 for k, v in dict(secret("locuteurs", {}) or {}).items()}
        code = table.get(email)
        code = code if code and FORMAT_CODE.match(code) else None
        admin = email in {str(a).strip().lower() for a in (secret("ADMIN_EMAILS", []) or [])}
        if not code and not admin:
            st.title("Collecte de dictées RadIA")
            st.warning(f"Le compte {email} n'est pas autorisé. Si vous participez à la collecte, "
                       "demandez au coordinateur de l'ajouter.")
            st.button("Se déconnecter", on_click=st.logout)
            return None
        st.sidebar.caption(email)
        st.sidebar.button("Se déconnecter", on_click=st.logout)
        return {"code": code, "admin": admin}

    # Mode "mot_de_passe" : essais locaux uniquement (tout le monde est administrateur)
    import hmac
    if not st.session_state.get("acces"):
        st.title("Collecte de dictées RadIA")
        attendu = secret("APP_PASSWORD")
        if not attendu:
            st.error("Aucun mot de passe configuré dans les secrets (voir LISEZMOI.md).")
            return None
        saisie = st.text_input("Mot de passe d'accès", type="password")
        if saisie:
            if hmac.compare_digest(saisie.encode(), str(attendu).encode()):
                st.session_state.acces = True
                st.rerun()
            st.error("Mot de passe incorrect.")
        return None
    code = st.sidebar.text_input("Votre code radiologue", placeholder="dr01").strip().lower()
    return {"code": code if FORMAT_CODE.match(code) else None, "admin": True}


# ─────────────────────────── Page : enregistrer ───────────────────────────

TEXTE_CONSENTEMENT = (
    "Vos enregistrements servent uniquement à améliorer la reconnaissance vocale de RadIA. "
    "Ils sont identifiés par un code pseudonyme, jamais par votre nom, avec le type de micro et le "
    "lieu que vous déclarez. Ils sont stockés chez un hébergeur cloud (Cloudflare), hors du Sénégal, "
    "avec un accès réservé au coordinateur du projet. Vous pouvez retirer votre consentement à tout "
    "moment auprès du coordinateur : vos enregistrements seront alors supprimés. Les modèles déjà "
    "entraînés avec ces enregistrements ne peuvent pas être modifiés rétroactivement ; ils ne "
    "seront plus réentraînés avec vos données."
)


def ecran_consentement(code: str) -> None:
    st.subheader("Consentement")
    st.markdown(TEXTE_CONSENTEMENT)
    ok = st.checkbox("J'accepte que ma voix soit enregistrée pour ce projet, dans ces conditions.")
    if st.button("Valider mon consentement", type="primary", disabled=not ok):
        ecrire_json(stockage(), f"consentements/{code}.json",
                    {"code": code, "version": VERSION_CONSENTEMENT, "horodatage": maintenant(),
                     "texte": TEXTE_CONSENTEMENT})
        st.rerun()


def ecran_seance(code: str) -> None:
    profil = lire_json(stockage(), f"profils/{code}.json", {})
    st.subheader("Avant de commencer")
    st.write("Ces informations aident à comprendre les erreurs du modèle selon le matériel et l'environnement.")
    with st.form("formulaire_seance"):
        micro = st.selectbox("Avec quel micro enregistrez-vous ?", MICROS,
                             index=MICROS.index(profil["micro"]) if profil.get("micro") in MICROS else 0)
        lieu = st.selectbox("Où êtes-vous ?", LIEUX,
                            index=LIEUX.index(profil["lieu"]) if profil.get("lieu") in LIEUX else 0)
        partage = st.checkbox("J'accepte de communiquer ma langue maternelle (facultatif)",
                              value=bool(profil.get("langue")))
        langue = st.text_input("Langue maternelle (facultatif)", value=profil.get("langue", ""))
        if st.form_submit_button("Commencer la séance", type="primary"):
            seance = {"micro": micro, "lieu": lieu, "langue": langue.strip() if partage else ""}
            ecrire_json(stockage(), f"profils/{code}.json", seance | {"horodatage": maintenant()})
            st.session_state.seance = seance
            st.session_state.faites_seance = 0
            st.rerun()


def phrase_courante(code: str, type_: str, modalite: str, deja: set):
    phrases, _ = banque()
    bloques = T.ids_en_erreur(problemes_banque())     # textes hors convention : jamais proposés
    selection = [p for p in phrases if p["type"] == type_ and p["id"] not in bloques
                 and (modalite == "Toutes" or p["modalite"] == modalite)]
    if code in test_locuteurs() and type_ == "phrase":
        selection = [p for p in selection if T.ensemble_de(p["id"]) == "test"]
    passees = st.session_state.setdefault("passees", set())
    restantes = [p for p in selection if p["id"] not in deja and p["id"] not in passees]
    st.session_state.selection_taille = (len(selection), sum(p["id"] in deja for p in selection))

    actuelle = st.session_state.get("phrase_courante")
    if actuelle and any(p["id"] == actuelle for p in restantes):
        return next(p for p in restantes if p["id"] == actuelle)
    couv = J.couverture(tous_les_journaux(), lexique())
    choix = T.choisir_phrase(restantes, couv, termes_par_id(), int(secret("CIBLE_VOIX", 5)),
                             graine=f"{code}-{len(deja)}")
    st.session_state.phrase_courante = choix["id"] if choix else None
    return choix


def page_enregistrer(ident: dict) -> None:
    code = ident["code"]
    if not code:
        st.title("Collecte de dictées RadIA")
        st.info("Aucun code radiologue n'est associé à ce compte." if ident["admin"] and
                str(secret("MODE_ACCES", "")).lower() == "google" else
                "Saisissez votre code radiologue (par exemple dr01) dans le panneau de gauche.")
        return
    consentement = lire_json(stockage(), f"consentements/{code}.json", {})
    if consentement.get("version") != VERSION_CONSENTEMENT:
        ecran_consentement(code)
        return
    if "seance" not in st.session_state:
        ecran_seance(code)
        return

    seance = st.session_state.seance
    st.sidebar.markdown(f"Code : **{code}**  \n{seance['micro']} · {seance['lieu']}")
    if st.sidebar.button("Changer de micro ou de lieu"):
        del st.session_state["seance"]
        st.rerun()
    mode = st.sidebar.radio("Que voulez-vous lire ?", ["Phrases courtes", "Comptes rendus complets"])
    type_ = "phrase" if mode == "Phrases courtes" else "compte_rendu"
    phrases, version = banque()
    modalite = st.sidebar.selectbox("Modalité", ["Toutes"] + sorted({p["modalite"] for p in phrases
                                                                    if p["type"] == type_}))
    if st.session_state.get("filtre") != (type_, modalite):
        st.session_state.filtre = (type_, modalite)
        st.session_state.phrase_courante = None

    deja = {j["id_phrase"] for j in journal_de(code)}
    phrase = phrase_courante(code, type_, modalite, deja)
    total, faites = st.session_state.selection_taille
    objectif = int(secret("OBJECTIF_SEANCE", 20))
    seance_faites = st.session_state.get("faites_seance", 0)

    st.title("Lisez le texte" if type_ == "phrase" else "Dictez ce compte rendu")
    st.progress(min(seance_faites / objectif, 1.0),
                text=f"Séance : {seance_faites} sur un objectif de {objectif} · "
                     f"au total {faites} sur {total} dans cette sélection")
    if msg := st.session_state.pop("message", None):
        st.success(msg)

    if phrase is None:
        st.success("Tous les textes de cette sélection sont enregistrés. Merci ! "
                   "Choisissez une autre modalité ou un autre type de lecture.")
        if st.session_state.get("passees") and st.button("Revoir les textes passés"):
            st.session_state.passees = set()
            st.rerun()
        return

    affichage = T.surligner_commandes(html.escape(phrase["texte"]))
    classe = "carte-phrase carte-cr" if type_ == "compte_rendu" else "carte-phrase"
    st.markdown(f'<div class="carte-meta">{html.escape(phrase["modalite"])} · {html.escape(phrase["id"])}</div>'
                f'<div class="{classe}">{affichage}</div>', unsafe_allow_html=True)
    st.caption("Les mots en couleur sont des commandes de ponctuation : dictez-les comme d'habitude."
               + (" Dictez à votre rythme naturel, comme en vacation." if type_ == "compte_rendu" else ""))

    cle = f"audio_{st.session_state.get('compteur', 0)}"
    enregistrement = st.audio_input("Appuyez sur le micro, lisez le texte, puis arrêtez.", key=cle)
    with st.expander("J'ai lu un peu différemment"):
        st.caption("Corrigez le texte pour qu'il corresponde exactement à ce que vous avez dit, "
                   "commandes comprises. Si l'écart est important, recommencez plutôt l'enregistrement.")
        texte_lu = st.text_area("Texte réellement lu", value=phrase["texte"], key=f"texte_{cle}")

    c1, c2, c3 = st.columns([2, 1, 1])
    enregistrer = c1.button("Enregistrer et passer au suivant", type="primary",
                            disabled=enregistrement is None, width="stretch")
    passer = c2.button("Passer", width="stretch")
    signaler = c3.button("Signaler le texte", width="stretch")

    def suivant(message=None):
        st.session_state.compteur = st.session_state.get("compteur", 0) + 1
        st.session_state.phrase_courante = None
        if message:
            st.session_state.message = message
        st.rerun()

    if passer:
        st.session_state.passees.add(phrase["id"])
        suivant()

    if signaler:
        ecrire_json(stockage(), f"signalements/{maintenant().replace(':', '-')}_{code}_{phrase['id']}.json",
                    {"horodatage": maintenant(), "code": code, "id_phrase": phrase["id"],
                     "texte": phrase["texte"]})
        st.session_state.passees.add(phrase["id"])
        suivant("Texte signalé au coordinateur. Merci.")

    if enregistrer and enregistrement is not None:
        texte_final = " ".join(texte_lu.split()) or phrase["texte"]
        erreurs = [m for n, m in T.verifier_texte(texte_final, type_, T.noms_propres_du_lexique(lexique()))
                   if n == "erreur"]
        if texte_final != phrase["texte"] and erreurs:
            st.error("Le texte corrigé ne respecte pas la convention d'écriture : " + " ; ".join(erreurs))
            return
        try:
            audio_brut = A.convertir_16k(enregistrement.getvalue())
        except Exception:
            st.error("L'enregistrement n'a pas pu être lu. Recommencez.")
            return
        audio, debut_coupe, fin_coupee = A.rogner(audio_brut)
        duree, alertes, blocages = A.controler(audio, type_, debut_coupe, fin_coupee)
        if blocages:
            for b in blocages:
                st.error(b)
            return
        nom = f"{code}_{phrase['id']}"
        nav, plat = infos_navigateur()
        entree = {
            "horodatage": maintenant(), "code": code, "id_phrase": phrase["id"], "type": type_,
            "modalite": phrase["modalite"], "fichier": f"brut/{nom}.wav",
            "duree_s": round(duree, 2), "duree_avant_rognage_s": round(len(audio_brut) / A.FREQ, 2),
            "texte_attendu": phrase["texte"], "texte_final": texte_final,
            "texte_modifie": texte_final != phrase["texte"], "alertes": " | ".join(alertes),
            "micro": seance["micro"], "lieu": seance["lieu"], "langue": seance.get("langue", ""),
            "navigateur": nav, "plateforme": plat,
            "capture": "st.audio_input (réglages par défaut du navigateur)",
            "version_banque": version, "version_app": VERSION_APP,
        }
        try:
            with st.spinner("Envoi de l'enregistrement…"):
                stockage().ecrire(f"brut/{nom}.wav", A.en_wav(audio))
                stockage().ecrire(f"brut/{nom}.txt", texte_final.encode("utf-8"))
                journal = [j for j in journal_de(code) if j["id_phrase"] != phrase["id"]] + [entree]
                ecrire_json(stockage(), f"journaux/{code}.json", journal)
        except Exception:
            st.error("L'envoi a échoué (connexion ou stockage). Votre enregistrement est toujours là : "
                     "réessayez dans un instant.")
            return
        tous_les_journaux.clear()
        st.session_state.faites_seance = st.session_state.get("faites_seance", 0) + 1
        msg = f"Enregistré ({duree:.1f} s)." + (" " + " ".join(alertes) if alertes else "")
        if st.session_state.faites_seance == objectif:
            msg += " Objectif de la séance atteint : merci ! Vous pouvez continuer ou vous arrêter là."
        suivant(msg)


# ─────────────────────────── Page : importer ───────────────────────────

STATUTS = {None: "en attente d'étiquetage", "etiquete": "étiquetée", "erreur": "erreur"}


@st.cache_data(show_spinner=False, max_entries=64)
def texte_de_fichier(nom: str, contenu: bytes) -> tuple:
    """(texte extrait, message d'erreur) d'un compte rendu déposé ; mis en cache entre deux affichages."""
    try:
        return I.extraire_texte(nom, contenu), ""
    except Exception as erreur:
        return "", f"{erreur}"[:300] or type(erreur).__name__


def _modalite_saisie(valeur: str) -> str:
    return "" if valeur == "Non précisée" else valeur


def page_importer(ident: dict) -> None:
    st.title("Importer des dictées")
    st.write("Déposez des dictées réalisées en pratique, avec leurs comptes rendus. Ils sont associés "
             "par le nom de fichier (**0001.m4a** avec **0001.docx**) ; sinon, vous les associez vous-même, ou "
             "vous collez le texte du compte rendu. Les noms de fichiers ne sont pas conservés.")
    st.caption(f"Audio : {', '.join(sorted(e[1:] for e in I.EXT_AUDIO))}. Compte rendu : "
               f"{', '.join(sorted(e[1:] for e in I.EXT_TEXTE))} (PDF contenant du texte). Ou une archive .zip. "
               f"Au plus {I.MAX_LOT} dictées par envoi, de 5 secondes à 30 minutes chacune.")
    if ident["admin"]:
        options = codes_connus()
        if not options:
            st.info("Aucun radiologue n'a encore de code ni de consentement.")
            return
        code = st.selectbox("Radiologue dont c'est la voix", options,
                            index=options.index(ident["code"]) if ident["code"] in options else 0)
        par = "coordinateur"
    else:
        code, par = ident["code"], "radiologue"
    if not code:
        st.info("Aucun code radiologue n'est associé à ce compte.")
        return
    if lire_json(stockage(), f"consentements/{code}.json", {}).get("version") != VERSION_CONSENTEMENT:
        st.warning(f"Le radiologue {code} doit d'abord accepter le consentement (page « Enregistrer »).")
        return

    modalites = ["Non précisée"] + sorted({p["modalite"] for p in banque()[0]}) + ["Autre"]
    modalite = st.selectbox("Modalité par défaut de ces dictées", modalites)
    cle = f"import_{st.session_state.get('compteur_import', 0)}"
    fichiers = st.file_uploader("Fichiers audio et comptes rendus", accept_multiple_files=True, key=cle,
                                type=sorted(e[1:] for e in I.EXT_AUDIO | I.EXT_TEXTE | I.EXT_ARCHIVE))
    if fichiers:
        importer_fichiers(code, par, fichiers, modalites, modalite, cle)
    if bilan := st.session_state.pop("bilan_import", None):
        ok = sum(b["résultat"] == "importée" for b in bilan)
        (st.success if ok == len(bilan) else st.warning)(f"{ok} dictée(s) importée(s) sur {len(bilan)}.")
        st.dataframe(pd.DataFrame(bilan), hide_index=True, width="stretch")

    st.subheader(f"Dictées importées pour {code}")
    index, etat = I.index_de(stockage(), code), I.etat_de(stockage(), code)
    if not index:
        st.caption("Aucune pour l'instant.")
        return
    lignes = [{"importée le": e["importe_le"][:10], "durée (min)": round(e["duree_s"] / 60, 1),
               "modalité": e.get("modalite") or "—",
               "état": STATUTS.get(etat.get(e["id"], {}).get("statut"), "en attente d'étiquetage"),
               "segments": etat.get(e["id"], {}).get("segments", ""),
               "à vérifier": etat.get(e["id"], {}).get("a_verifier", "")} for e in reversed(index)]
    st.dataframe(pd.DataFrame(lignes), hide_index=True, width="stretch")
    st.caption("L'étiquetage est lancé par le coordinateur. Il découpe chaque dictée en segments, écarte les "
               "passages où un nom, une date ou un âge est prononcé, et signale les segments à relire.")
    corriger_dictee(code, index, modalites)


A_ASSOCIER, COLLER, ECARTER = "À associer…", "Coller le texte du compte rendu", "Ne pas importer"


def importer_fichiers(code: str, par: str, fichiers: list, modalites: list, modalite: str, cle: str) -> None:
    fichiers, problemes_zip = I.deballer(fichiers)
    paires, audios_seuls, textes_seuls, problemes = I.apparier_detail(fichiers)
    for pb in problemes_zip + problemes:
        st.warning(pb)
    dictees = [{"audio": a, "cr": t} for a, t in paires]
    attente = []          # pourquoi des dictées ne sont pas prêtes : affiché au-dessus du bouton
    bloque = False

    if audios_seuls:
        st.subheader("Associer les dictées restantes")
        st.caption("Ces audios n'ont pas de compte rendu du même nom : choisissez le bon fichier, collez le "
                   "texte du compte rendu, ou écartez l'audio.")
        noms_cr = [t.name for t in textes_seuls]
        pris, a_associer = {}, 0
        for a in audios_seuls:
            choix = st.selectbox(a.name, [A_ASSOCIER] + noms_cr + [COLLER, ECARTER], key=f"{cle}_assoc_{a.name}")
            if choix == A_ASSOCIER:
                a_associer += 1
            elif choix == COLLER:
                dictees.append({"audio": a, "cr": None})
            elif choix != ECARTER:
                pris[choix] = pris.get(choix, 0) + 1
                dictees.append({"audio": a, "cr": next(t for t in textes_seuls if t.name == choix)})
        if a_associer:
            attente.append(f"{a_associer} audio(s) sans compte rendu : choisissez-le dans « Associer les dictées "
                           "restantes »")
        if doublons := [n for n, k in pris.items() if k > 1]:
            st.error("Un même compte rendu est associé à plusieurs dictées : " + ", ".join(doublons))
            attente.append("un compte rendu est associé à plusieurs dictées")
            bloque = True
        if inutilises := [n for n in noms_cr if n not in pris]:
            st.caption("Comptes rendus non utilisés : " + ", ".join(inutilises))
    elif textes_seuls:
        st.caption("Comptes rendus sans audio, ignorés : " + ", ".join(t.name for t in textes_seuls))
    if not paires and not audios_seuls:
        attente.append("aucun fichier audio déposé")

    prets = []
    if dictees:
        st.subheader("Vérifier avant d'importer")
    for k, d in enumerate(dictees):
        a = d["audio"]
        titre = f"{a.name}  +  {d['cr'].name if d['cr'] else 'texte collé'}"
        with st.expander(titre, expanded=len(dictees) <= 3):
            mod = st.selectbox("Modalité", modalites, index=modalites.index(modalite), key=f"{cle}_mod_{k}")
            if st.toggle("Écouter l'audio", key=f"{cle}_ecoute_{k}"):
                st.audio(a.getvalue())
            if d["cr"] is None:
                texte, erreur = st.text_area("Texte du compte rendu", key=f"{cle}_colle_{k}", height=180), ""
            else:
                texte, erreur = texte_de_fichier(d["cr"].name, d["cr"].getvalue())
            if erreur:
                st.error(f"Compte rendu illisible : {erreur}")
                attente.append(f"{a.name} : compte rendu illisible")
            elif not texte.strip():
                if d["cr"] is None:
                    st.info("Collez le texte du compte rendu ci-dessus, puis cliquez ailleurs pour le valider.")
                    attente.append(f"{a.name} : texte du compte rendu à coller")
                else:
                    st.warning("Compte rendu vide (PDF scanné ?) : il faut un fichier contenant du texte.")
                    attente.append(f"{a.name} : compte rendu vide")
            else:
                gardees, retirees = I.apercu_compte_rendu(texte)
                if retirees:
                    st.caption(f"{len(retirees)} ligne(s) d'en-tête ignorée(s) à l'étiquetage : "
                               + " · ".join(retirees))
                st.text("\n".join(gardees)[:3000])
                prets.append((a, texte, _modalite_saisie(mod)))

    if len(prets) > I.MAX_LOT:
        attente.append(f"au plus {I.MAX_LOT} dictées par envoi : importez en plusieurs fois")
        bloque = True
    st.divider()
    st.write(f"**{len(prets)}** dictée(s) prête(s) à importer.")
    for raison in attente:
        st.caption(f"• {raison}")
    if st.button(f"Importer {len(prets)} dictée(s)", type="primary", disabled=not prets or bloque,
                 key=f"{cle}_importer"):
        barre, bilan = st.progress(0.0, text="Import en cours…"), []
        for k, (fa, texte, mod) in enumerate(prets):
            try:
                audio = A.convertir_16k(fa.getvalue())
                ident_d, motif = I.enregistrer(stockage(), code, A.en_wav(audio), len(audio) / A.FREQ, texte, mod, par)
            except Exception as erreur:
                ident_d, motif = None, f"fichier illisible ({type(erreur).__name__})"
            bilan.append({"fichier": fa.name, "résultat": "importée" if ident_d else motif})
            barre.progress((k + 1) / len(prets), text=f"{k + 1} / {len(prets)}")
        st.session_state.bilan_import = bilan
        st.session_state.compteur_import = st.session_state.get("compteur_import", 0) + 1
        st.rerun()


def corriger_dictee(code: str, index: list, modalites: list) -> None:
    st.subheader("Corriger une dictée importée")
    if message := st.session_state.pop("message_correction", None):
        st.success(message)
    if TR.travail_en_cours(stockage()):
        st.info("Un étiquetage est en cours : les corrections seront possibles quand il sera terminé.")
        return
    libelles = {e["id"]: f"{e['importe_le'][:10]} · {round(e['duree_s'] / 60, 1)} min · "
                         f"{e.get('modalite') or 'modalité non précisée'} · {e['id']}" for e in reversed(index)}
    ident_d = st.selectbox("Dictée", list(libelles), format_func=libelles.get, key="correction_dictee")
    entree = next(e for e in index if e["id"] == ident_d)
    if st.toggle("Écouter et relire le compte rendu", key=f"corr_voir_{ident_d}"):
        audio = stockage().lire(f"historique/audio/{ident_d}.wav")
        if audio:
            st.audio(audio, format="audio/wav")
        st.text((stockage().lire(f"historique/cr/{ident_d}.txt") or b"").decode("utf-8")[:3000])

    actuelle = entree.get("modalite") or "Non précisée"
    choix = modalites if actuelle in modalites else modalites + [actuelle]
    mod = st.selectbox("Modalité", choix, index=choix.index(actuelle), key=f"corr_mod_{ident_d}")
    if st.button("Enregistrer la modalité", disabled=mod == actuelle):
        I.modifier_dictee(stockage(), code, ident_d, modalite=_modalite_saisie(mod))
        tous_les_journaux.clear()
        st.session_state.message_correction = "Modalité enregistrée."
        st.rerun()

    st.markdown("**Mauvais compte rendu ?** Déposez le bon fichier ou collez son texte. Les segments déjà "
                "étiquetés de cette dictée sont effacés : elle sera ré-étiquetée au prochain lancement.")
    nouveau = st.file_uploader("Bon compte rendu", type=sorted(e[1:] for e in I.EXT_TEXTE), key=f"corr_fichier_{ident_d}")
    colle = st.text_area("ou texte du compte rendu", key=f"corr_texte_{ident_d}", height=120)
    if st.button("Remplacer le compte rendu", disabled=not (nouveau or colle.strip())):
        texte, erreur = texte_de_fichier(nouveau.name, nouveau.getvalue()) if nouveau else (colle, "")
        refus = erreur or I.modifier_dictee(stockage(), code, ident_d, texte_cr=texte)
        if refus:
            st.error(f"Compte rendu non remplacé : {refus}")
        else:
            tous_les_journaux.clear()
            st.session_state.message_correction = "Compte rendu remplacé : la dictée sera ré-étiquetée."
            st.rerun()

    confirme = st.checkbox("Je confirme la suppression de cette dictée, de son compte rendu et de ses segments",
                           key=f"corr_confirme_{ident_d}")
    if st.button("Supprimer la dictée", disabled=not confirme):
        n = I.supprimer_dictee(stockage(), code, ident_d)
        tous_les_journaux.clear()
        st.session_state.message_correction = (f"Dictée supprimée ({n} segment(s) retiré(s)). Créez un nouvel "
                                               "instantané pour qu'elle disparaisse aussi des jeux de données.")
        st.rerun()


# ─────────────────────────── Page : consignes ───────────────────────────

def page_consignes() -> None:
    st.title("Consignes d'enregistrement")
    st.markdown("""
**Pour que vos enregistrements soient utiles :**

- Enregistrez de préférence **dans votre salle de lecture, avec votre micro de dictée habituel**. Le bruit de fond normal est souhaité : c'est dans ces conditions que le modèle travaillera.
- **Dictez les commandes de ponctuation** (affichées en couleur : « virgule », « point à la ligne »...) exactement comme en vacation.
- Lisez **à votre rythme de dictée habituel**, sans articuler plus que d'ordinaire.
- **Ne prononcez jamais de nom de patient** ni aucune information permettant d'identifier quelqu'un.
- Lancez l'enregistrement, attendez une demi-seconde, lisez **le texte en entier**, puis arrêtez.
- Si vous butez sur un mot, **recommencez l'enregistrement** plutôt que de corriger le texte.
- Un texte vous semble faux, ambigu ou mal écrit ? Utilisez **Signaler le texte**.

**Deux types de lecture :**

- **Phrases courtes** : une phrase par enregistrement, quelques secondes. C'est l'essentiel de la collecte.
- **Comptes rendus complets** : un compte rendu entier, dicté à votre rythme naturel. Ils servent à vérifier le modèle dans de vraies conditions de dictée.

Une séance de 20 textes prend environ 5 à 10 minutes.
""")


# ─────────────────────────── Page : administration ───────────────────────────

def admin_vue(df: pd.DataFrame) -> None:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Enregistrements", len(df))
    c2.metric("Durée totale", f"{df['duree_s'].sum() / 3600:.2f} h")
    c3.metric("Voix", df["code"].nunique())
    c4.metric("Comptes rendus", int((df["type"] == "compte_rendu").sum()))
    st.subheader("Par radiologue et par modalité")
    st.dataframe(df.pivot_table(index="code", columns="modalite", values="fichier",
                                aggfunc="count", fill_value=0), width="stretch")
    st.subheader("Par micro et par lieu")
    st.dataframe(df.pivot_table(index="micro", columns="lieu", values="fichier",
                                aggfunc="count", fill_value=0), width="stretch")
    if test_locuteurs():
        st.caption("Voix réservées au test (jamais en entraînement) : " + ", ".join(sorted(test_locuteurs())))
    cles = stockage().lister("signalements/")
    if cles:
        st.subheader("Textes signalés")
        st.dataframe(pd.DataFrame([lire_json(stockage(), c, {}) for c in cles[-200:]]),
                     width="stretch", hide_index=True)


def admin_couverture(journal: list) -> None:
    cible = int(secret("CIBLE_VOIX", 5))
    couv = J.couverture(journal, lexique())
    phrases, _ = banque()
    lignes = [{"terme": l["terme"], "catégorie": l["categorie"], "voix": couv.get(l["terme"], 0),
               "textes dans la banque": sum(l["terme"] in termes_par_id()[p["id"]] for p in phrases)}
              for l in lexique()]
    if not lignes:
        st.info("Le fichier lexique.csv est absent ou vide.")
        return
    df = pd.DataFrame(lignes).sort_values(["voix", "terme"])
    atteints = int((df["voix"] >= cible).sum())
    st.metric(f"Termes prononcés par au moins {cible} voix", f"{atteints} sur {len(df)}")
    st.caption("Les textes contenant les termes les moins couverts sont proposés en priorité aux radiologues. "
               "Un terme présent dans aucun texte de la banque ne peut pas progresser : ajoutez des phrases.")
    st.dataframe(df, width="stretch", hide_index=True,
                 column_config={"voix": st.column_config.ProgressColumn(
                     "voix", min_value=0, max_value=cible, format="%d")})


def admin_a_verifier(journal: list) -> None:
    resultats = lire_json(stockage(), J.CLE_RESULTATS, {})
    decisions = lire_json(stockage(), J.CLE_DECISIONS, {})
    par_fichier = {j["fichier"]: j for j in journal}
    a_voir = [f for f, r in resultats.items() if r["statut"] == "a_verifier"
              and f not in decisions and f in par_fichier]
    non_valides = sum(1 for f in par_fichier if f not in resultats)
    st.write(f"**{len(a_voir)}** enregistrement(s) à vérifier · {non_valides} pas encore passé(s) "
             "par la validation automatique (scripts/valider_enregistrements.py).")
    if not a_voir:
        return
    classes = J.trier_a_verifier(a_voir, par_fichier, resultats, lexique())
    infos = {f: (points, motifs) for f, points, motifs in classes}
    st.caption("Classés par valeur : d'abord les erreurs critiques du modèle de base, les termes du lexique "
               "qu'il a manqués et les corrections du compte rendu à confirmer ; en dernier, les comptes "
               "rendus peut-être mal appariés.")
    fichier = st.selectbox(
        "Enregistrement à vérifier", [f for f, _, _ in classes],
        format_func=lambda f: f"{infos[f][0]:+d} · {(infos[f][1] or ['écart global'])[0]} · {Path(f).stem}")
    j, r = par_fichier[fichier], resultats[fichier]
    if infos[fichier][1]:
        st.caption("Pourquoi ici : " + " · ".join(infos[fichier][1]))
    audio = stockage().lire(fichier)
    if audio:
        st.audio(audio, format="audio/wav")
    historique = j.get("source") == "historique"
    st.markdown(f"**{'Texte proposé' if historique else 'Texte attendu'} :** {html.escape(j['texte_final'])}")
    st.markdown(f"**{'Transcriptions des moteurs' if historique else 'Transcription du modèle de base'} :** "
                f"{html.escape(r.get('hypothese', ''))}")
    if historique:
        st.caption("Segment d'une dictée réelle : le texte proposé combine Parakeet, Whisper et le compte rendu. "
                   "Corrigez-le pour qu'il corresponde exactement à ce qui est dit, commandes comprises.")
    st.caption(f"CER {r.get('cer', 0):.0%} · motifs : {', '.join(r.get('raisons', [])) or 'écart global'}")
    corrige = st.text_area("Si le radiologue a dit autre chose, corrigez le texte ici",
                           value=j["texte_final"], key=f"corr_{fichier}")
    c1, c2, c3 = st.columns(3)

    def decider(statut_, texte=None):
        decisions[fichier] = {"statut": statut_, "horodatage": maintenant()} | (
            {"texte_corrige": texte} if texte else {})
        ecrire_json(stockage(), J.CLE_DECISIONS, decisions)
        if texte:
            stockage().ecrire(fichier.replace(".wav", ".txt"), texte.encode("utf-8"))
            jj = lire_json(stockage(), cle_journal(j), [])
            for e in jj:
                if e["fichier"] == fichier:
                    e["texte_final"], e["texte_modifie"], e["corrige_par_coordinateur"] = texte, True, True
            ecrire_json(stockage(), cle_journal(j), jj)
            tous_les_journaux.clear()
        st.rerun()

    if c1.button("Conforme", type="primary", width="stretch"):
        decider("valide_humain")
    if c2.button("Enregistrer la correction", width="stretch"):
        texte = " ".join(corrige.split())
        err = [m for n, m in T.verifier_texte(texte, j.get("type", "phrase"),
                                              T.noms_propres_du_lexique(lexique())) if n == "erreur"]
        if err:
            st.error("Texte non conforme : " + " ; ".join(err))
        else:
            decider("valide_humain", texte)
    if c3.button("Rejeter", width="stretch"):
        decider("rejete")


def admin_enregistrements(df: pd.DataFrame) -> None:
    resultats = lire_json(stockage(), J.CLE_RESULTATS, {})
    decisions = lire_json(stockage(), J.CLE_DECISIONS, {})
    choix = st.selectbox("Enregistrement", df.sort_values("horodatage", ascending=False)["fichier"].tolist())
    ligne = df[df["fichier"] == choix].iloc[0]
    audio = stockage().lire(choix)
    if audio:
        st.audio(audio, format="audio/wav")
    st.write(ligne["texte_final"])
    st.caption(f"{ligne['code']} · {ligne['modalite']} · {ligne['type']} · {ligne['duree_s']:.1f} s · "
               f"{ligne['micro']} · {ligne['lieu']} · validation : {J.statut(choix, resultats, decisions)}"
               + (f" · {ligne['alertes']}" if ligne["alertes"] else ""))
    confirmer = st.checkbox("Je confirme la suppression de cet enregistrement")
    if st.button("Supprimer", disabled=not confirmer):
        stockage().supprimer(choix)
        stockage().supprimer(choix.replace(".wav", ".txt"))
        cle_j = cle_journal(ligne.to_dict())
        reste = [j for j in lire_json(stockage(), cle_j, []) if j["fichier"] != choix]
        ecrire_json(stockage(), cle_j, reste)
        tous_les_journaux.clear()
        st.success("Supprimé. Le texte sera reproposé à ce radiologue.")
        st.rerun()


def admin_jeux(journal: list) -> None:
    exiger = bool(secret("EXIGER_VALIDATION", False))
    st.write("Un **instantané** fige l'état des données à un instant donné. Il n'est jamais modifié : "
             "chaque modèle entraîné référence l'instantané utilisé.")
    st.caption(f"Ensembles : {', '.join(J.ENSEMBLES)}. Répartition déterministe par identifiant de phrase ; "
               "les comptes rendus vont dans test_comptes_rendus ; les voix réservées dans test_voix_nouvelles. "
               f"Validation exigée : {'oui' if exiger else 'non (seuls les rejets et les « à vérifier » sont exclus)'}.")
    if st.button("Créer un instantané", type="primary"):
        r = J.creer_instantane(stockage(), journal, test_locuteurs(), banque()[1], exiger)
        st.success(f"Instantané {r['instantane']} créé.")
    for nom in J.lister_instantanes(stockage())[:10]:
        r = lire_json(stockage(), f"manifestes/{nom}/resume.json", {})
        with st.expander(nom):
            if r:
                st.dataframe(pd.DataFrame(r["ensembles"]).T, width="stretch")
                st.caption(f"banque {r['version_banque']} · app {r['version_app']} · "
                           f"exclus : {r['exclus'] or 'aucun'}")


def admin_retrait(journal: list) -> None:
    codes = sorted({j["code"] for j in journal} |
                   {c.split("/")[-1][:-5] for c in stockage().lister("consentements/")} |
                   {c.split("/")[-1][:-5] for c in stockage().lister("historique/index/")})
    if not codes:
        st.info("Aucun radiologue enregistré.")
        return
    code = st.selectbox("Radiologue qui retire son consentement", codes)
    st.warning("Supprime définitivement ses enregistrements, ses dictées importées et leurs segments, son journal, "
               "son consentement et son profil. "
               "Retirez aussi son compte des Secrets, puis créez un nouvel instantané.")
    saisie = st.text_input(f"Tapez « {code} » pour confirmer")
    if st.button("Tout supprimer pour ce radiologue", disabled=saisie.strip().lower() != code):
        segments_hist = {j["fichier"] for j in lire_json(stockage(), f"journaux/hist_{code}.json", [])}
        cles = stockage().lister(f"brut/{code}_")
        for c in cles + [f"journaux/{code}.json", f"consentements/{code}.json", f"profils/{code}.json"]:
            stockage().supprimer(c)
        n_hist = I.supprimer_code(stockage(), code)
        a_retirer = lambda f: f.startswith(f"brut/{code}_") or f in segments_hist   # noqa: E731
        J.maj_resultats(stockage(), retires=[f for f in lire_json(stockage(), J.CLE_RESULTATS, {}) if a_retirer(f)])
        d = lire_json(stockage(), J.CLE_DECISIONS, {})
        ecrire_json(stockage(), J.CLE_DECISIONS, {f: v for f, v in d.items() if not a_retirer(f)})
        tous_les_journaux.clear()
        st.success(f"{len(cles) + n_hist} fichier(s) supprimé(s) pour {code}.")


def admin_dictees_reelles() -> None:
    lignes = []
    for cle in stockage().lister("historique/index/"):
        code = cle.split("/")[-1][:-5]
        etat = I.etat_de(stockage(), code)
        for e in I.index_de(stockage(), code):
            et = etat.get(e["id"], {})
            lignes.append({"code": code, "durée_h": e["duree_s"] / 3600, "statut": et.get("statut") or "en_attente",
                           "segments": et.get("segments", 0), "à vérifier": et.get("a_verifier", 0),
                           "concordance": et.get("concordance")})
    if not lignes:
        st.info("Aucune dictée importée. Les radiologues ou le coordinateur utilisent la page « Importer des dictées ».")
    else:
        df = pd.DataFrame(lignes)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Dictées importées", len(df))
        c2.metric("Durée", f"{df['durée_h'].sum():.1f} h")
        c3.metric("En attente", int((df["statut"] == "en_attente").sum()))
        c4.metric("Segments produits", int(df["segments"].sum()))
        st.dataframe(df.groupby(["code", "statut"]).agg(dictées=("statut", "size"), heures=("durée_h", "sum"),
                                                      segments=("segments", "sum"), a_verifier=("à vérifier", "sum"))
                     .round(2), width="stretch")
        faibles = df[df["concordance"].notna() & (df["concordance"] < 0.3)]
        if len(faibles):
            st.warning(f"{len(faibles)} dictée(s) concordent très peu avec leur compte rendu : appariement à vérifier.")

    st.subheader("Étiquetage sur GPU")
    travail = lire_json(stockage(), TR.CLE_TRAVAIL, {})
    if travail:
        total = travail.get("total") or 0
        texte = (f"Travail {travail.get('id_travail', '')} : {travail.get('statut')} · "
                 f"{travail.get('traitees', 0)} / {total if total else '…'} dictée(s) · "
                 f"{travail.get('segments', 0)} segment(s) · {travail.get('a_verifier', 0)} à vérifier · "
                 f"{travail.get('erreurs', 0)} erreur(s) · mis à jour {str(travail.get('maj', ''))[:16]}")
        if travail.get("statut") == "en_cours" and total:
            st.progress(travail.get("traitees", 0) / total, text=texte)
        else:
            st.caption(texte)
    url, jeton = secret("MODAL_URL"), secret("MODAL_JETON")
    if not (url and jeton):
        st.info("MODAL_URL et MODAL_JETON ne sont pas configurés dans les Secrets (voir LISEZMOI.md, « Étiquetage sur GPU »).")
        return
    relancer = st.checkbox("Retraiter aussi les dictées en erreur")
    validation = J.validation_en_cours(stockage())
    if validation:
        st.info("Une validation (valider_enregistrements.py) est en cours sur la machine GPU : l'étiquetage "
                "pourra être lancé quand elle sera terminée.")
    if st.button("Lancer l'étiquetage", type="primary", disabled=TR.travail_en_cours(stockage()) or validation):
        import requests
        try:
            rep_ = requests.post(url, json={"test_locuteurs": sorted(test_locuteurs()), "relancer_erreurs": relancer},
                                 headers={"Authorization": f"Bearer {jeton}"}, timeout=60)
        except requests.RequestException:
            st.error("Le service GPU ne répond pas. Réessayez dans un instant.")
            return
        if rep_.status_code == 200:
            st.success("Étiquetage lancé. Le GPU démarre (une à deux minutes), puis l'avancement s'affiche ici : "
                       "cliquez sur « Actualiser les données ».")
        elif rep_.status_code == 409:
            try:
                motif = rep_.json().get("detail") or "un étiquetage est déjà en cours"
            except ValueError:
                motif = "un étiquetage est déjà en cours"
            st.warning(f"Lancement refusé : {motif}.")
        else:
            st.error(f"Le service GPU a refusé la demande (code {rep_.status_code}).")
    st.caption("Le GPU traite toutes les dictées en attente, puis s'éteint. Les segments douteux apparaissent "
               "dans l'onglet « À vérifier ». Les dictées réservées au test y sont toutes : chaque segment doit "
               "être relu pour entrer dans test_historique.")


def afficher_controle_banque() -> None:
    problemes = problemes_banque()
    erreurs = [p for p in problemes if p[0] == "erreur"]
    avertissements = [p for p in problemes if p[0] != "erreur"]
    if erreurs:
        bloques = T.ids_en_erreur(problemes)
        st.error(f"**Banque de textes : {len(erreurs)} erreur(s) de convention.** "
                 f"{len(bloques)} texte(s) ne sont plus proposés à la lecture tant que phrases.csv "
                 "n'est pas corrigé (voir docs/convention-ecriture.md).")
        with st.expander("Détail des erreurs"):
            for _, ou, message in erreurs:
                st.markdown(f"- **{ou}** : {message}")
    if avertissements:
        with st.expander(f"Banque de textes : {len(avertissements)} avertissement(s)"):
            for _, ou, message in avertissements:
                st.markdown(f"- **{ou}** : {message}")
    if not problemes:
        st.caption(f"Banque de textes conforme : {len(banque()[0])} texte(s), {len(lexique())} terme(s) "
                   f"du lexique, version {banque()[1]}.")


def page_administration(ident: dict) -> None:
    st.title("Administration")
    if not ident["admin"]:
        st.info("Cet espace est réservé au coordinateur.")
        return
    if st.button("Actualiser les données"):
        tous_les_journaux.clear()
    journal = tous_les_journaux()
    afficher_controle_banque()
    onglets = st.tabs(["Vue d'ensemble", "Couverture", "À vérifier", "Enregistrements",
                       "Dictées réelles", "Jeux de données", "Retrait de consentement"])
    with onglets[4]:
        admin_dictees_reelles()
    if not journal:
        with onglets[0]:
            st.info("Aucun enregistrement pour l'instant.")
        with onglets[1]:
            admin_couverture([])
        return
    df = pd.DataFrame(journal)
    for col, defaut in [("type", "phrase"), ("micro", ""), ("lieu", ""), ("alertes", ""), ("source", "lecture")]:
        df[col] = df[col].fillna(defaut) if col in df else defaut
    with onglets[0]:
        admin_vue(df)
    with onglets[1]:
        admin_couverture(journal)
    with onglets[2]:
        admin_a_verifier(journal)
    with onglets[3]:
        admin_enregistrements(df)
    with onglets[5]:
        admin_jeux(journal)
    with onglets[6]:
        admin_retrait(journal)


# ─────────────────────────── Navigation ───────────────────────────

ident = identifier()
if ident:
    pages = ["Enregistrer", "Importer des dictées", "Consignes"] + (["Administration"] if ident["admin"] else [])
    page = st.sidebar.radio("Navigation", pages)
    if page == "Enregistrer":
        page_enregistrer(ident)
    elif page == "Importer des dictées":
        page_importer(ident)
    elif page == "Consignes":
        page_consignes()
    else:
        page_administration(ident)
