"""Servislerin işlem sınırı: varsayılan `engine.begin()`, testlerde geri alınan iç işlem verilebilir."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager

from sqlalchemy import Connection, Engine
from sqlalchemy.exc import IntegrityError

from medakis.repositories.errors import translate

TransactionFactory = Callable[[], AbstractContextManager[Connection]]


def factory_for(engine: Engine) -> TransactionFactory:
    return engine.begin


@contextmanager
def unit_of_work(begin: TransactionFactory) -> Iterator[Connection]:
    """İşlemi açar; kısıt ihlalini alan hatasına çevirir. Hata olursa işlem geri alınır."""
    try:
        with begin() as conn:
            yield conn
    except IntegrityError as exc:
        raise translate(exc) from None
