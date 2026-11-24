"""prova.ps1 çıkış kodu doğrulaması (B4): yalnız ortadaki (postgres) grupta bilinçli hata enjeksiyonu."""

import pytest


def test_varsayilan_grup():
    assert True


@pytest.mark.postgres
def test_postgres_grubu_enjekte_hata():
    raise AssertionError("bilinçli hata enjeksiyonu")


@pytest.mark.postgres_lifecycle
def test_lifecycle_grubu():
    assert True


@pytest.mark.prova_rol
def test_prova_rol_grubu():
    assert True
