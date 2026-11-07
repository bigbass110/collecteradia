"""Stockage des enregistrements : dossier local (essais) ou Cloudflare R2 (production)."""
import json
from pathlib import Path


class StockageLocal:
    def __init__(self, racine):
        self.racine = Path(racine)

    def ecrire(self, cle: str, donnees: bytes) -> None:
        chemin = self.racine / cle
        chemin.parent.mkdir(parents=True, exist_ok=True)
        tmp = chemin.with_name(chemin.name + ".tmp")
        tmp.write_bytes(donnees)
        tmp.replace(chemin)

    def lire(self, cle: str):
        chemin = self.racine / cle
        return chemin.read_bytes() if chemin.exists() else None

    def lister(self, prefixe: str) -> list:
        base = self.racine / prefixe
        dossier = base if base.is_dir() else base.parent
        if not dossier.exists():
            return []
        cles = (str(p.relative_to(self.racine)).replace("\\", "/")
                for p in dossier.rglob("*") if p.is_file() and not p.name.endswith(".tmp"))
        return sorted(c for c in cles if c.startswith(prefixe))

    def supprimer(self, cle: str) -> None:
        (self.racine / cle).unlink(missing_ok=True)


CODES_ABSENT = {"NoSuchKey", "404", "NotFound"}
CONSEILS = {
    "AccessDenied": "le jeton R2 n'a pas les droits sur ce compartiment (lecture et écriture), ou il a été supprimé",
    "InvalidAccessKeyId": "l'identifiant de clé R2 (access_key_id) est inconnu : jeton supprimé ou mal recopié",
    "SignatureDoesNotMatch": "la clé secrète R2 (secret_access_key) ne correspond pas à l'identifiant de clé",
    "NoSuchBucket": "le compartiment indiqué (bucket) n'existe pas dans ce compte Cloudflare",
    "Unauthorized": "les clés R2 sont refusées : vérifiez account_id, access_key_id et secret_access_key",
}


class ErreurStockage(RuntimeError):
    """Le stockage refuse l'opération. Le message (code R2 et conseil) ne contient aucune clé ni donnée :
    il peut être affiché tel quel."""

    def __init__(self, operation: str, code: str):
        self.code = code
        conseil = CONSEILS.get(code, "vérifiez les clés R2 dans les Secrets de l'application")
        super().__init__(f"Le stockage Cloudflare R2 refuse l'opération « {operation} » (code {code}) : {conseil}.")


def _code_erreur(exc) -> str:
    reponse = getattr(exc, "response", None) or {}
    return str((reponse.get("Error") or {}).get("Code") or (reponse.get("ResponseMetadata") or {}).get("HTTPStatusCode")
               or type(exc).__name__)


class StockageR2:
    def __init__(self, conf: dict, client=None):
        self.bucket = conf["bucket"]
        if client is None:
            import boto3
            client = boto3.client(
                "s3",
                endpoint_url=conf.get("endpoint_url")
                or f"https://{conf['account_id']}.r2.cloudflarestorage.com",
                aws_access_key_id=conf["access_key_id"],
                aws_secret_access_key=conf["secret_access_key"],
                region_name="auto",
            )
        self.s3 = client

    def ecrire(self, cle: str, donnees: bytes) -> None:
        try:
            self.s3.put_object(Bucket=self.bucket, Key=cle, Body=donnees)
        except Exception as exc:
            raise ErreurStockage("écriture", _code_erreur(exc)) from exc

    def lire(self, cle: str):
        try:
            return self.s3.get_object(Bucket=self.bucket, Key=cle)["Body"].read()
        except Exception as exc:
            code = _code_erreur(exc)
            if code in CODES_ABSENT:
                return None
            raise ErreurStockage("lecture", code) from exc

    def lister(self, prefixe: str) -> list:
        cles, jeton = [], None
        while True:
            args = {"Bucket": self.bucket, "Prefix": prefixe}
            if jeton:
                args["ContinuationToken"] = jeton
            try:
                rep = self.s3.list_objects_v2(**args)
            except Exception as exc:
                raise ErreurStockage("liste", _code_erreur(exc)) from exc
            cles += [o["Key"] for o in rep.get("Contents", [])]
            if not rep.get("IsTruncated"):
                return sorted(cles)
            jeton = rep["NextContinuationToken"]

    def supprimer(self, cle: str) -> None:
        try:
            self.s3.delete_object(Bucket=self.bucket, Key=cle)
        except Exception as exc:
            raise ErreurStockage("suppression", _code_erreur(exc)) from exc


def lire_json(stockage, cle: str, defaut):
    brut = stockage.lire(cle)
    return json.loads(brut) if brut else defaut


def ecrire_json(stockage, cle: str, valeur) -> None:
    stockage.ecrire(cle, json.dumps(valeur, ensure_ascii=False, indent=1).encode("utf-8"))


def depuis_environnement():
    """Pour les scripts : R2 si les variables R2_* existent, sinon dossier local ./donnees."""
    import os
    if os.environ.get("R2_BUCKET"):
        return StockageR2({
            "bucket": os.environ["R2_BUCKET"],
            "account_id": os.environ["R2_ACCOUNT_ID"],
            "access_key_id": os.environ["R2_ACCESS_KEY_ID"],
            "secret_access_key": os.environ["R2_SECRET_ACCESS_KEY"],
        })
    return StockageLocal(os.environ.get("DOSSIER_DONNEES", "donnees"))
