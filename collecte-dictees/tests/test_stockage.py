import io

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from collecte.stockage import ErreurStockage, StockageR2, lire_json


class FauxS3:
    """Client S3 minimal : renvoie un contenu, ou lève l'erreur demandée."""

    def __init__(self, erreur=None, contenu=b'{"a": 1}'):
        self.erreur, self.contenu = erreur, contenu

    def _peut_etre(self):
        if self.erreur:
            raise self.erreur

    def get_object(self, **k):
        self._peut_etre()
        return {"Body": io.BytesIO(self.contenu)}

    def put_object(self, **k):
        self._peut_etre()

    def list_objects_v2(self, **k):
        self._peut_etre()
        return {"Contents": [{"Key": "b"}, {"Key": "a"}]}

    def delete_object(self, **k):
        self._peut_etre()


def client_error(code, statut=403):
    return ClientError({"Error": {"Code": code, "Message": "secret"}, "ResponseMetadata": {"HTTPStatusCode": statut}},
                       "GetObject")


def r2(erreur=None):
    return StockageR2({"bucket": "radia"}, client=FauxS3(erreur))


def test_lecture_normale_et_fichier_absent():
    assert lire_json(r2(), "x.json", {}) == {"a": 1}
    assert r2().lister("") == ["a", "b"]
    for code, statut in (("NoSuchKey", 404), ("404", 404), ("NotFound", 404)):
        assert r2(client_error(code, statut)).lire("absent.json") is None


@pytest.mark.parametrize("code, conseil", [
    ("AccessDenied", "droits"), ("InvalidAccessKeyId", "access_key_id"),
    ("SignatureDoesNotMatch", "secret_access_key"), ("NoSuchBucket", "compartiment"),
])
def test_refus_traduit_en_message_lisible(code, conseil):
    with pytest.raises(ErreurStockage) as e:
        r2(client_error(code)).lire("consentements/dr01.json")
    assert e.value.code == code and f"code {code}" in str(e.value) and conseil in str(e.value)
    assert "secret" not in str(e.value).replace("secret_access_key", "")      # message R2 brut jamais repris


def test_toutes_les_operations_et_erreur_reseau():
    refus = client_error("AccessDenied")
    for operation in (lambda s: s.ecrire("k", b"x"), lambda s: s.lister(""), lambda s: s.supprimer("k")):
        with pytest.raises(ErreurStockage, match="AccessDenied"):
            operation(r2(refus))
    with pytest.raises(ErreurStockage, match="EndpointConnectionError"):
        r2(EndpointConnectionError(endpoint_url="https://x")).lire("k")
