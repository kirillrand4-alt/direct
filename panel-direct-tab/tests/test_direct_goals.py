"""Подбор целей Метрики: опознание по идентификатору условия, а не по названию."""
from __future__ import annotations

import os
import sys
import tempfile

os.environ["DATABASE_URL"] = "sqlite:///" + tempfile.mktemp(suffix=".db")
os.environ.setdefault("ENABLE_SCHEDULER", "false")
sys.path.insert(0, os.getcwd())

from app.db.base import Base, SessionLocal, engine  # noqa: E402
from app.db import models, models_direct  # noqa: E402,F401
from app.services import direct_goals as dg  # noqa: E402


def setup_module(_=None):
    Base.metadata.create_all(engine)


def _goal(gid, name, ident):
    return {"id": gid, "name": name, "type": "action",
            "conditions": [{"type": "exact", "url": ident}]}


PAYLOAD = {"goals": [
    _goal(1, "[Roistat] Емейлтрекинг", "emailtracking"),
    _goal(2, "[Roistat] Все лиды", "lead_status"),
    _goal(3, "[Roistat] Квалифицированный лид", "qualified_lead"),
    # название сломано человеком — по имени такую цель не найти
    _goal(4, "Roistat] Сделка успешна", "deal_success"),
    _goal(5, "[Roistat] Спам лид", "spam_lead"),
    _goal(6, "Посмотрел 5 страниц", "pageviews5"),
]}


def test_goals_of_indexes_by_condition(monkeypatch):
    monkeypatch.setattr(dg, "goals_of", lambda c: {
        g["conditions"][0]["url"]: (str(g["id"]), g["name"]) for g in PAYLOAD["goals"]})
    got = dg.goals_of(1)
    assert got["qualified_lead"] == ("3", "[Roistat] Квалифицированный лид")
    assert got["deal_success"][1] == "Roistat] Сделка успешна"


def test_suggest_orders_series_and_finds_broken_name(monkeypatch):
    """Порядок серий фиксирован: первой идёт та, под которую собираются разрезы."""
    monkeypatch.setattr(dg, "counter_for", lambda db, d: 4242)
    monkeypatch.setattr(dg, "goals_of", lambda c: {
        g["conditions"][0]["url"]: (str(g["id"]), g["name"]) for g in PAYLOAD["goals"]})
    db = SessionLocal()
    try:
        text, report = dg.suggest(db, "d.example")
        assert text == "Квал=3\nЛид 1=2\nСпам=5\nСделка=4", text
        assert any("счётчик 4242" in line for line in report)
    finally:
        db.close()


def test_suggest_reports_missing_goals(monkeypatch):
    monkeypatch.setattr(dg, "counter_for", lambda db, d: 7)
    monkeypatch.setattr(dg, "goals_of", lambda c: {"lead_status": ("11", "Все лиды")})
    db = SessionLocal()
    try:
        text, report = dg.suggest(db, "d.example")
        assert text == "Лид 1=11"
        assert any("Квал" in line and "нет" in line for line in report)
    finally:
        db.close()


def test_suggest_without_counter(monkeypatch):
    monkeypatch.setattr(dg, "counter_for", lambda db, d: None)
    db = SessionLocal()
    try:
        text, report = dg.suggest(db, "d.example")
        assert text == "" and "счётчик" in report[0]
    finally:
        db.close()


def test_apply_spares_manual_sets(monkeypatch):
    """Подбор — подсказка; осознанно выбранные цели важнее её."""
    monkeypatch.setattr(dg, "counter_for", lambda db, d: 1)
    monkeypatch.setattr(dg, "goals_of", lambda c: {"qualified_lead": ("3", "Квал")})
    written = {}
    monkeypatch.setattr("app.credentials.set_cred", lambda k, v: written.__setitem__(k, v))
    monkeypatch.setattr("app.credentials.get_cred",
                        lambda k, default=None: "Своё=9" if k.startswith("direct_goal_sets:") else default)
    db = SessionLocal()
    try:
        assert dg.apply_suggested(db, ["d.example"]) == []
        assert written == {}
        assert dg.apply_suggested(db, ["d.example"], overwrite=True) == [
            ("d.example", "Квал=3")]
        assert written["direct_goal_sets:d.example"] == "Квал=3"
    finally:
        db.close()


def test_counter_for_reads_domain_from_start_url():
    """Счётчик определяется по адресу входа: site_id у визитов произвольный."""
    from app.db.models import Visit
    from datetime import date

    db = SessionLocal()
    try:
        for i in range(3):
            db.add(Visit(site_id=999, visit_id="v%d" % i, counter_id=555,
                         date=date(2026, 8, 1), start_url="https://www.mine.example/a"))
        db.add(Visit(site_id=999, visit_id="vx", counter_id=777,
                     date=date(2026, 8, 1), start_url="https://other.example/a"))
        db.commit()
        assert dg.counter_for(db, "mine.example") == 555
        assert dg.counter_for(db, "nobody.example") is None
    finally:
        db.close()
