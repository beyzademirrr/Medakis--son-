"""prova.ps1 çıkış kodu doğrulaması (B4): her grupta bir test seçilir, hepsi geçer.

tests/ dışında olduğu için tests/conftest.py (dev parmak izi) yüklenmez; veritabanına bağlanılmaz.
"""

import pytest


def test_varsayilan_grup():
    assert True


@pytest.mark.postgres
def test_postgres_grubu():
    assert True


@pytest.mark.postgres_lifecycle
def test_lifecycle_grubu():
    assert True


@pytest.mark.prova_rol
def test_prova_rol_grubu():
    assert True
