"""Несколько целей рядом: ``goal_key`` в ключе истории.

Проверяется разбор наборов, раздельное хранение серий, перестройка таблиц под
новый ключ и то, что разрезы собираются один раз, а не на каждую цель.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date

os.environ["DATABASE_URL"] = "sqlite:///" + tempfile.mktemp(suffix=".db")
os.environ.setdefault("ENABLE_SCHEDULER", "false")
sys.path.insert(0, os.getcwd())

from sqlalchemy import text  # noqa: E402

from app.db.base import Base, SessionLocal, engine  # noqa: E402
from app.db import models, models_direct  # noqa: E402,F401
from app.services import direct_collect, direct_store as store  # noqa: E402

D = date(2026, 8, 1)


def setup_module(_=None):
    Base.metadata.create_all(engine)


def _creds(mapping):
    def fake(key, default=None):
        return mapping.get(key, default)
    return fake


def _row(day, clicks, conv, campaign="111"):
    return [{"Date": day.isoformat(), "CampaignId": campaign, "CampaignName": "Поиск",
             "Impressions": "1000", "Clicks": str(clicks), "Cost": "100.00",
             "Conversions": str(conv)}]


# ------------------------------- разбор наборов ------------------------------ #

def test_goal_sets_parsed(monkeypatch):
    monkeypatch.setattr("app.credentials.get_cred", _creds({
        "direct_goal_sets:d.ru": "Лид 1=123456789\nКвал = 987654321, 111\nмусор\n"}))
    assert direct_collect.goal_sets("d.ru") == [
        ("Лид 1", ["123456789"]), ("Квал", ["987654321", "111"])]


def test_goal_sets_fall_back_to_single_goal(monkeypatch):
    """Домен со старой одиночной настройкой попадает в серию с пустым ключом —
    уже собранная история остаётся видимой, а не уезжает под новое имя."""
    monkeypatch.setattr("app.credentials.get_cred",
                        _creds({"direct_goals:d.ru": "123456789"}))
    assert direct_collect.goal_sets("d.ru") == [("", ["123456789"])]


def test_goal_sets_empty_means_all_goals(monkeypatch):
    monkeypatch.setattr("app.credentials.get_cred", _creds({}))
    assert direct_collect.goal_sets("d.ru") == [("", [])]


def test_goal_sets_ignore_duplicates_and_junk(monkeypatch):
    monkeypatch.setattr("app.credentials.get_cred", _creds({
        "direct_goal_sets:d.ru": "Лид=1\nЛид=2\n=3\nПустой=\n"}))
    assert direct_collect.goal_sets("d.ru") == [("Лид", ["1"])]


# ------------------------------ раздельные серии ----------------------------- #

def test_series_do_not_overwrite_each_other():
    """Две цели за один день — две строки, а не одна перезаписанная."""
    db = SessionLocal()
    try:
        dom = "series.example"
        store.save_daily(db, dom, "", _row(D, 10, 4), "Лид 1")
        store.save_daily(db, dom, "", _row(D, 10, 1), "Квал")
        db.commit()
        assert store.totals(db, dom, D, D, "", "Лид 1")["conversions"] == 4
        assert store.totals(db, dom, D, D, "", "Квал")["conversions"] == 1
        # клики и расход в сериях одни и те же — задваиваться они не должны
        assert store.totals(db, dom, D, D, "", "Лид 1")["clicks"] == 10
    finally:
        db.close()


def test_campaigns_and_breakdown_separated_by_goal():
    db = SessionLocal()
    try:
        dom = "camp.example"
        store.save_campaigns(db, dom, "", _row(D, 10, 7), "Лид 1")
        store.save_campaigns(db, dom, "", _row(D, 10, 2), "Квал")
        raw = [{"Date": D.isoformat(), "CampaignId": "1", "Query": "купить компрессор",
                "Impressions": "50", "Clicks": "5", "Cost": "40", "Conversions": "3"}]
        store.save_breakdown(db, dom, "query", "", raw, None, "Query", "Лид 1")
        db.commit()
        assert store.campaigns(db, dom, D, D, "", "Лид 1")[0]["conversions"] == 7
        assert store.campaigns(db, dom, D, D, "", "Квал")[0]["conversions"] == 2
        assert store.breakdown(db, dom, "query", D, D, "", goal_key="Лид 1")[0]["conversions"] == 3
        # под другой ключ разрез не собирался — там пусто, а не чужие цифры
        assert store.breakdown(db, dom, "query", D, D, "", goal_key="Квал") == []
    finally:
        db.close()


def test_upsert_still_idempotent_within_a_series():
    db = SessionLocal()
    try:
        dom = "idem.example"
        store.save_daily(db, dom, "", _row(D, 10, 4), "Лид 1")
        store.save_daily(db, dom, "", _row(D, 8, 3), "Лид 1")
        db.commit()
        t = store.totals(db, dom, D, D, "", "Лид 1")
        assert t["clicks"] == 8 and t["conversions"] == 3
    finally:
        db.close()


# -------------------------------- сбор по сериям ----------------------------- #

class _Provider:
    """Заглушка провайдера: считает, сколько раз спрашивали каждый отчёт."""

    BREAKDOWNS = {"query": ("SEARCH_QUERY_PERFORMANCE_REPORT", None, "Query")}

    def __init__(self):
        self.daily_goals = []
        self.breakdown_calls = 0

    def daily(self, domain, dr, goals=None, attribution=None):
        self.daily_goals.append(goals)
        return _row(dr.end, 10, 5 if goals else 99)

    def campaigns_daily(self, domain, dr, goals=None, attribution=None):
        return _row(dr.end, 10, 5 if goals else 99)

    def breakdown_daily(self, domain, dr, kind, goals=None, attribution=None):
        self.breakdown_calls += 1
        return [{"Date": dr.end.isoformat(), "CampaignId": "1", "Query": "фраза",
                 "Impressions": "10", "Clicks": "1", "Cost": "5", "Conversions": "1"}]


def test_collect_walks_every_goal_set_but_breaks_down_once(monkeypatch):
    """Разрезы — только под первый набор: копия на каждую цель удвоила бы
    самую объёмную таблицу истории ради одной колонки."""
    db = SessionLocal()
    try:
        dom = "collect.example"
        monkeypatch.setattr("app.credentials.get_cred", _creds({
            "direct_goal_sets:%s" % dom: "Лид 1=111\nКвал=222",
            "direct_breakdowns:%s" % dom: "query"}))
        provider = _Provider()
        monkeypatch.setattr(direct_collect, "YandexDirectProvider", lambda: provider)
        monkeypatch.setattr(direct_collect.YandexDirectProvider, "BREAKDOWNS",
                            _Provider.BREAKDOWNS, raising=False)
        monkeypatch.setattr(direct_collect, "_flag", lambda *a: False)
        monkeypatch.setattr("app.services.direct_changes.sync", lambda *a, **k: 0)

        direct_collect.collect(db, dom, direct_collect._Range(D, D), job_type="manual")
        db.commit()
        assert provider.daily_goals == [["111"], ["222"]]
        assert provider.breakdown_calls == 1
        assert store.totals(db, dom, D, D, "", "Лид 1")["conversions"] == 5
        assert store.totals(db, dom, D, D, "", "Квал")["conversions"] == 5
    finally:
        db.close()


# --------------------------------- миграция ---------------------------------- #

def test_rebuild_adds_goal_key_and_keeps_rows():
    """Старая таблица без goal_key пересобирается, строки переносятся."""
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS direct_daily__old_shape"))
        conn.execute(text("""
            CREATE TABLE direct_daily__old_shape (
                id INTEGER NOT NULL PRIMARY KEY,
                domain VARCHAR(255) NOT NULL, date DATE NOT NULL,
                attribution VARCHAR(8) NOT NULL DEFAULT '',
                impressions INTEGER, clicks INTEGER, cost FLOAT, conversions INTEGER,
                CONSTRAINT uq_x UNIQUE (domain, date, attribution))"""))
        conn.execute(text("INSERT INTO direct_daily__old_shape "
                          "(domain, date, attribution, impressions, clicks, cost, conversions) "
                          "VALUES ('old.example', '2026-08-01', '', 1, 2, 3.0, 4)"))

    # подменяем описание так, чтобы пересобрать именно эту таблицу
    spec = models_direct._REBUILD_WITH_GOAL_KEY["direct_daily"]
    models_direct._REBUILD_WITH_GOAL_KEY["direct_daily__old_shape"] = spec
    try:
        models_direct._rebuild_for_goal_key(engine, "direct_daily__old_shape")
    finally:
        models_direct._REBUILD_WITH_GOAL_KEY.pop("direct_daily__old_shape", None)

    with engine.begin() as conn:
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(direct_daily__old_shape)"))}
        assert "goal_key" in cols
        row = conn.execute(text("SELECT domain, goal_key, conversions "
                                "FROM direct_daily__old_shape")).one()
        assert row == ("old.example", "", 4)


def test_ensure_schema_is_idempotent():
    """Повторный вызов на уже правильной схеме ничего не ломает и не теряет."""
    db = SessionLocal()
    try:
        before = db.execute(text("SELECT COUNT(*) FROM direct_daily")).scalar_one()
    finally:
        db.close()
    models_direct.ensure_direct_schema(engine)
    models_direct.ensure_direct_schema(engine)
    db = SessionLocal()
    try:
        assert db.execute(text("SELECT COUNT(*) FROM direct_daily")).scalar_one() == before
    finally:
        db.close()


def test_refetch_window_covers_offline_conversions():
    """Окно перезабора должно перекрывать срок доезда офлайн-конверсий.

    Квалификация лида и статус сделки приходят из CRM через Roistat спустя
    недели после визита; Директ принимает их до 21 дня назад. С окном в 14 дней
    конверсии, доехавшие на 15–21 день, не попадали в историю никогда.
    """
    assert direct_collect.REFETCH_DAYS >= 21, direct_collect.REFETCH_DAYS
