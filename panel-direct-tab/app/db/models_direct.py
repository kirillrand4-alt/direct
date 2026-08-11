"""Таблицы истории Яндекс Директа.

Отдельный модуль, а не дописывание в ``app/db/models.py``: так обновление не
конфликтует с чужими правками основного файла моделей. Модели наследуют тот же
``Base``, поэтому ``Base.metadata.create_all`` создаёт их вместе с остальными —
достаточно, чтобы модуль был импортирован до создания схемы (это делает
``app/api/routes_direct.py``, который импортируется при старте приложения).

Гранулярность — сутки, как у остальных метрик панели: любой период считается
``WHERE date BETWEEN ...`` с агрегацией, поэтому недели, месяцы и сравнение
периодов получаются из одних и тех же строк.

Ключ домена — строка, а не FK на ``site``. Аккаунт Директа привязан к домену, а
не к «свойству» поисковой системы; так же сделано в ``url_brand``.

**Про ``attribution`` в уникальном ключе.** Модель атрибуции меняет числа в
``conversions``. Если бы её не было в ключе, смена модели в настройках тихо
перезаписывала бы историю числами по другой методике, и график «за год» смешивал
бы несравнимое. С ней строки разных моделей живут рядом, а запросы фильтруют по
той, что выбрана сейчас. Для аккаунта без целей значение — пустая строка.
"""
from __future__ import annotations

from datetime import date as date_type
from datetime import datetime, timezone

from sqlalchemy import (
    Date,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DirectDaily(Base):
    """Итоги аккаунта за сутки (ACCOUNT_PERFORMANCE_REPORT)."""

    __tablename__ = "direct_daily"
    __table_args__ = (
        UniqueConstraint("domain", "date", "attribution", name="uq_direct_daily"),
        Index("ix_direct_daily_dom_date", "domain", "date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    domain: Mapped[str] = mapped_column(String(255), index=True)
    date: Mapped[date_type] = mapped_column(Date)
    attribution: Mapped[str] = mapped_column(String(8), default="")
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float] = mapped_column(Float, default=0.0)
    conversions: Mapped[int] = mapped_column(Integer, default=0)


class DirectCampaignDaily(Base):
    """Кампания за сутки (CAMPAIGN_PERFORMANCE_REPORT с полем Date)."""

    __tablename__ = "direct_campaign_daily"
    __table_args__ = (
        UniqueConstraint("domain", "date", "campaign_id", "attribution",
                         name="uq_direct_campaign_daily"),
        Index("ix_direct_camp_dom_date", "domain", "date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    domain: Mapped[str] = mapped_column(String(255), index=True)
    date: Mapped[date_type] = mapped_column(Date)
    campaign_id: Mapped[str] = mapped_column(String(32))
    campaign_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    attribution: Mapped[str] = mapped_column(String(8), default="")
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float] = mapped_column(Float, default=0.0)
    conversions: Mapped[int] = mapped_column(Integer, default=0)


class DirectBreakdownDaily(Base):
    """Один срез за сутки: фраза, поисковый запрос, устройство или регион.

    Все разрезы в одной таблице, а не в четырёх: строки одинаковой формы
    (ключ + метрики), различаются только полем ``kind``. Новый разрез добавляется
    записью в ``YandexDirectProvider.BREAKDOWNS`` и не требует миграции.

    ``key_hash`` — потому что фраза или поисковый запрос могут быть длиннее, чем
    разумно индексировать; уникальность держится на хеше, а читаемый текст лежит
    рядом в ``key_text``.
    """

    __tablename__ = "direct_breakdown_daily"
    __table_args__ = (
        UniqueConstraint("domain", "date", "kind", "key_hash", "attribution",
                         name="uq_direct_breakdown_daily"),
        Index("ix_direct_brk_dom_kind_date", "domain", "kind", "date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    domain: Mapped[str] = mapped_column(String(255), index=True)
    date: Mapped[date_type] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(16))  # criteria | query | device | region
    key_hash: Mapped[str] = mapped_column(String(64))
    key_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    key_text: Mapped[str] = mapped_column(Text)
    campaign_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    attribution: Mapped[str] = mapped_column(String(8), default="")
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float] = mapped_column(Float, default=0.0)
    conversions: Mapped[int] = mapped_column(Integer, default=0)


class DirectCollectRun(Base):
    """Журнал сборов Директа.

    Своя таблица, а не общий ``collection_run``: тот привязан внешними ключами к
    ``source``/``site``, а у Директа нет ни того, ни другого — он живёт на домене.
    Нужна для инкрементального окна (откуда продолжать) и для показа состояния.
    """

    __tablename__ = "direct_collect_run"
    __table_args__ = (Index("ix_direct_run_dom_started", "domain", "started_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    domain: Mapped[str] = mapped_column(String(255), index=True)
    job_type: Mapped[str] = mapped_column(String(16), default="daily")  # daily | backfill | manual
    target_date: Mapped[date_type | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | ok | error
    rows_written: Mapped[int] = mapped_column(Integer, default=0)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
