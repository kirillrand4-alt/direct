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

import logging

from sqlalchemy import (
    Date,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Index,
    inspect,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# Колонки, добавленные к таблицам, которые у кого-то уже существуют.
# ``create_all`` создаёт таблицы, но не меняет существующие, а alembic в панели
# нет — поэтому такие колонки дописываем сами.
_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "direct_account": {
        "clicks_90d": "INTEGER DEFAULT 0",
        "cost_90d": "FLOAT DEFAULT 0",
    },
}


# Таблицы, у которых ``goal_key`` входит в уникальный ключ. Такую колонку
# нельзя просто дописать: ключ зашит в CREATE TABLE, а SQLite не умеет менять
# ограничения. Поэтому таблица пересобирается — новая схема, перелив строк,
# подмена. Значения перечислены целиком, чтобы DDL читался, а не собирался
# метапрограммированием из моделей: одна опечатка тут стоит истории.
_REBUILD_WITH_GOAL_KEY: dict[str, tuple[str, list[str]]] = {
    "direct_daily": ("""
        CREATE TABLE {new} (
            id INTEGER NOT NULL PRIMARY KEY,
            domain VARCHAR(255) NOT NULL,
            date DATE NOT NULL,
            goal_key VARCHAR(32) NOT NULL DEFAULT '',
            attribution VARCHAR(8) NOT NULL DEFAULT '',
            impressions INTEGER, clicks INTEGER, cost FLOAT, conversions INTEGER,
            CONSTRAINT uq_direct_daily UNIQUE (domain, date, goal_key, attribution)
        )""", ["domain", "date", "attribution", "impressions", "clicks", "cost",
               "conversions"]),
    "direct_campaign_daily": ("""
        CREATE TABLE {new} (
            id INTEGER NOT NULL PRIMARY KEY,
            domain VARCHAR(255) NOT NULL,
            date DATE NOT NULL,
            campaign_id VARCHAR(32) NOT NULL,
            campaign_name VARCHAR(512),
            goal_key VARCHAR(32) NOT NULL DEFAULT '',
            attribution VARCHAR(8) NOT NULL DEFAULT '',
            impressions INTEGER, clicks INTEGER, cost FLOAT, conversions INTEGER,
            CONSTRAINT uq_direct_campaign_daily
                UNIQUE (domain, date, campaign_id, goal_key, attribution)
        )""", ["domain", "date", "campaign_id", "campaign_name", "attribution",
               "impressions", "clicks", "cost", "conversions"]),
    "direct_breakdown_daily": ("""
        CREATE TABLE {new} (
            id INTEGER NOT NULL PRIMARY KEY,
            domain VARCHAR(255) NOT NULL,
            date DATE NOT NULL,
            kind VARCHAR(16) NOT NULL,
            key_hash VARCHAR(64) NOT NULL,
            key_id VARCHAR(32),
            key_text TEXT NOT NULL,
            campaign_id VARCHAR(32),
            goal_key VARCHAR(32) NOT NULL DEFAULT '',
            attribution VARCHAR(8) NOT NULL DEFAULT '',
            impressions INTEGER, clicks INTEGER, cost FLOAT, conversions INTEGER,
            CONSTRAINT uq_direct_breakdown_daily
                UNIQUE (domain, date, kind, key_hash, goal_key, attribution)
        )""", ["domain", "date", "kind", "key_hash", "key_id", "key_text", "campaign_id",
               "attribution", "impressions", "clicks", "cost", "conversions"]),
}

# Индексы пересобираемых таблиц — при DROP TABLE они исчезают вместе с ней.
_REBUILD_INDEXES: dict[str, list[tuple[str, str]]] = {
    "direct_daily": [("ix_direct_daily_dom_date", "(domain, date)"),
                     ("ix_direct_daily_domain", "(domain)")],
    "direct_campaign_daily": [("ix_direct_camp_dom_date", "(domain, date)"),
                              ("ix_direct_campaign_daily_domain", "(domain)")],
    "direct_breakdown_daily": [("ix_direct_brk_dom_kind_date", "(domain, kind, date)"),
                               ("ix_direct_breakdown_daily_domain", "(domain)")],
}


def _rebuild_for_goal_key(engine, table: str) -> None:
    """Пересобрать таблицу так, чтобы ``goal_key`` вошёл в уникальный ключ.

    Строки переносятся как есть с ``goal_key=''``. Пустой ключ означает «как
    собирали раньше» — по всем целям аккаунта либо по единственной заданной
    цели; отделить одно от другого задним числом нечем, поэтому и не
    выдумываем. Новые серии лягут рядом под своими ключами.
    """
    ddl, columns = _REBUILD_WITH_GOAL_KEY[table]
    new = f"{table}__goalkey"
    cols = ", ".join(columns)
    with engine.begin() as conn:
        conn.execute(text(f"DROP TABLE IF EXISTS {new}"))
        conn.execute(text(ddl.format(new=new)))
        conn.execute(text(
            f"INSERT INTO {new} ({cols}, goal_key) SELECT {cols}, '' FROM {table}"))
        moved = conn.execute(text(f"SELECT COUNT(*) FROM {new}")).scalar_one()
        conn.execute(text(f"DROP TABLE {table}"))
        conn.execute(text(f"ALTER TABLE {new} RENAME TO {table}"))
        for name, cols_sql in _REBUILD_INDEXES.get(table, []):
            conn.execute(text(f"CREATE INDEX IF NOT EXISTS {name} ON {table} {cols_sql}"))
    logger.info("Директ: %s пересобрана под goal_key, перенесено строк %s", table, moved)


def ensure_direct_schema(engine) -> None:
    """Привести схему таблиц Директа к текущей версии вкладки.

    Идемпотентно: сверяется с фактической схемой и делает только недостающее.
    Без этого обновление вкладки на работающей панели падало бы на
    ``no such column`` — таблицы созданы прошлой версией и живут со старым
    набором колонок.
    """
    try:
        inspector = inspect(engine)
        existing_tables = set(inspector.get_table_names())
    except Exception:  # noqa: BLE001 — схема не критична для остальной панели
        logger.exception("Директ: не удалось прочитать схему БД")
        return

    for table, columns in _ADDED_COLUMNS.items():
        if table not in existing_tables:
            continue  # create_all создаст её сразу правильной
        have = {c["name"] for c in inspector.get_columns(table)}
        for name, ddl in columns.items():
            if name in have:
                continue
            try:
                with engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
                logger.info("Директ: в %s добавлена колонка %s", table, name)
            except Exception:  # noqa: BLE001
                logger.exception("Директ: не удалось добавить %s.%s", table, name)

    for table in _REBUILD_WITH_GOAL_KEY:
        if table not in existing_tables:
            continue
        if "goal_key" in {c["name"] for c in inspector.get_columns(table)}:
            continue
        try:
            _rebuild_for_goal_key(engine, table)
        except Exception:  # noqa: BLE001
            logger.exception("Директ: не удалось пересобрать %s под goal_key", table)


class DirectDaily(Base):
    """Итоги аккаунта за сутки (ACCOUNT_PERFORMANCE_REPORT)."""

    __tablename__ = "direct_daily"
    __table_args__ = (
        UniqueConstraint("domain", "date", "goal_key", "attribution", name="uq_direct_daily"),
        Index("ix_direct_daily_dom_date", "domain", "date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    domain: Mapped[str] = mapped_column(String(255), index=True)
    date: Mapped[date_type] = mapped_column(Date)
    goal_key: Mapped[str] = mapped_column(String(32), default="")
    attribution: Mapped[str] = mapped_column(String(8), default="")
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float] = mapped_column(Float, default=0.0)
    conversions: Mapped[int] = mapped_column(Integer, default=0)


class DirectCampaignDaily(Base):
    """Кампания за сутки (CAMPAIGN_PERFORMANCE_REPORT с полем Date)."""

    __tablename__ = "direct_campaign_daily"
    __table_args__ = (
        UniqueConstraint("domain", "date", "campaign_id", "goal_key", "attribution",
                         name="uq_direct_campaign_daily"),
        Index("ix_direct_camp_dom_date", "domain", "date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    domain: Mapped[str] = mapped_column(String(255), index=True)
    date: Mapped[date_type] = mapped_column(Date)
    campaign_id: Mapped[str] = mapped_column(String(32))
    campaign_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    goal_key: Mapped[str] = mapped_column(String(32), default="")
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
        UniqueConstraint("domain", "date", "kind", "key_hash", "goal_key", "attribution",
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
    goal_key: Mapped[str] = mapped_column(String(32), default="")
    attribution: Mapped[str] = mapped_column(String(8), default="")
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float] = mapped_column(Float, default=0.0)
    conversions: Mapped[int] = mapped_column(Integer, default=0)


class DirectSettingsSnapshot(Base):
    """Последнее известное состояние объекта Директа (кампания, корректировка…).

    Хранится **только текущий** снапшот на объект, а не история снапшотов: история
    живёт в ``direct_change`` в виде отличий. Иначе таблица росла бы на весь
    аккаунт каждую ночь, отдавая при этом ту же информацию.

    ``payload`` — канонический JSON (ключи отсортированы), ``payload_hash`` — его
    хеш: сравнение хешей отсекает неизменившиеся объекты, не разбирая JSON.
    """

    __tablename__ = "direct_settings_snapshot"
    __table_args__ = (
        UniqueConstraint("domain", "object_type", "object_id", name="uq_direct_snapshot"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    domain: Mapped[str] = mapped_column(String(255), index=True)
    object_type: Mapped[str] = mapped_column(String(24))  # campaign | bid_modifier | keyword_bid
    object_id: Mapped[str] = mapped_column(String(32))
    object_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    payload: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class DirectChange(Base):
    """Журнал изменений настроек — то, чего API Директа не отдаёт.

    Читаемой истории изменений в API v5 нет: ``Changes.check`` возвращает только
    идентификаторы изменившихся объектов и метку времени. Поэтому журнал строим
    сами, сравнивая свежий снапшот с предыдущим.

    ``detected_at`` — когда **мы заметили** изменение, а не когда его внесли.
    Точное время правки Директ не сообщает, и выдавать одно за другое нельзя:
    при суточном сборе правка попадает в журнал следующей ночью.
    """

    __tablename__ = "direct_change"
    __table_args__ = (
        Index("ix_direct_change_dom_at", "domain", "detected_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    domain: Mapped[str] = mapped_column(String(255), index=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    object_type: Mapped[str] = mapped_column(String(24))
    object_id: Mapped[str] = mapped_column(String(32))
    object_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    kind: Mapped[str] = mapped_column(String(16), default="changed")  # added | changed | removed
    field: Mapped[str] = mapped_column(String(64))
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)


class DirectAccount(Base):
    """Найденный рекламный кабинет: логин, чей он и на какой домен рекламирует.

    Зачем таблица, если привязка домен→логин живёт в кредах
    (``direct_login:<домен>``). Креды — это ключ-значение без обратного порядка:
    по ним нельзя показать «какие кабинеты вообще есть, что в них и какие из них
    ещё не привязаны». Разведка находит кабинеты пачкой, и её результат нужно
    показать человеку **до** записи привязок — иначе кабинет-дубль тихо
    перезапишет рабочий.

    ``domain`` — доминирующий домен из ссылок объявлений. Определять по имени
    логина нельзя: Яндекс выдаёт логины вида ``brand-a-100001-k7x2`` и
    ``auto-a1b2c3d4``, в них домена нет вовсе.
    """

    __tablename__ = "direct_account"
    __table_args__ = (
        UniqueConstraint("login", name="uq_direct_account_login"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    login: Mapped[str] = mapped_column(String(128), index=True)
    client_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    client_info: Mapped[str | None] = mapped_column(String(255), nullable=True)
    campaigns: Mapped[int] = mapped_column(Integer, default=0)
    live_campaigns: Mapped[int] = mapped_column(Integer, default=0)
    # клики и расход за последние месяцы — признак того, что кабинет рабочий,
    # а не пустой дубль с тем же доменом
    clicks_90d: Mapped[int] = mapped_column(Integer, default=0)
    cost_90d: Mapped[float] = mapped_column(Float, default=0.0)
    # доминирующий домен и полный счётчик хостов из ссылок объявлений (JSON)
    domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    domains: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="ok")  # ok | error
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    scanned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


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
