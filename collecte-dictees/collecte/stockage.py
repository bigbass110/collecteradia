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
        self.s3.put_object(Bucket=self.bucket, Key=cle, Body=donnees)

    def lire(self, cle: str):
        try:
            return self.s3.get_object(Bucket=self.bucket, Key=cle)["Body"].read()
        except self.s3.exceptions.NoSuchKey:
            return None

    def lister(self, prefixe: str) -> list:
        cles, jeton = [], None
        while True:
            args = {"Bucket": self.bucket, "Prefix": prefixe}
            if jeton:
                args["ContinuationToken"] = jeton
            rep = self.s3.list_objects_v2(**args)
            cles += [o["Key"] for o in rep.get("Contents", [])]
            if not rep.get("IsTruncated"):
                return sorted(cles)
            jeton = rep["NextContinuationToken"]

    def supprimer(self, cle: str) -> None:
        self.s3.delete_object(Bucket=self.bucket, Key=cle)


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
