"""Проверка журнала изменений настроек Директа.

Ответы сервисов подменяются — проверяется логика снапшота и сравнения, а не
доступность API.
"""
from __future__ import annotations

import os
import sys
import tempfile

# ВАЖНО: настройка называется database_url → переменная DATABASE_URL.
# С неверным именем тесты писали бы в боевую БД панели и копили состояние.
os.environ["DATABASE_URL"] = "sqlite:///" + tempfile.mktemp(suffix=".db")
os.environ.setdefault("ENABLE_SCHEDULER", "false")
sys.path.insert(0, os.getcwd())

from app.db.base import Base, SessionLocal, engine  # noqa: E402
from app.db import models, models_direct  # noqa: E402,F401
from app.services import direct_changes as ch  # noqa: E402

D = "chg.example"


def _campaign(cid="111", name="Поиск — Москва", state="ON", budget=5000, **extra):
    row = {
        "Id": cid, "Name": name, "Type": "TEXT_CAMPAIGN", "State": state,
        "Status": "ACCEPTED", "StatusPayment": "ALLOWED",
        "DailyBudget": {"Amount": budget * 1_000_000, "Mode": "STANDARD"},
        "NegativeKeywords": {"Items": ["бесплатно", "б у"]},
    }
    row.update(extra)
    return row


def setup_module(_=None):
    Base.metadata.create_all(engine)


def test_first_run_is_silent():
    """Первый прогон только запоминает состояние: журнал не должен открываться
    сотней строк «появилось» про всё, что и так было.

    Свой домен: тесты идут по алфавиту и делили бы состояние друг друга.
    """
    db = SessionLocal()
    try:
        dom = "firstrun.example"
        changes = ch.sync_object_type(db, dom, "campaign",
                                      [_campaign(), _campaign("222", "РСЯ")])
        db.commit()
        assert changes == [], changes
        assert ch.recent(db, dom) == []
    finally:
        db.close()


def test_budget_change_is_recorded_in_rubles():
    """Дневной бюджет приходит в микроединицах — в журнале должны быть рубли."""
    db = SessionLocal()
    try:
        ch.sync_object_type(db, D, "campaign", [_campaign(budget=5000), _campaign("222", "РСЯ")])
        db.commit()
        ch.sync_object_type(db, D, "campaign", [_campaign(budget=3000), _campaign("222", "РСЯ")])
        db.commit()

        rows = [c for c in ch.recent(db, D) if c.field == "DailyBudget"]
        assert len(rows) == 1, rows
        assert "5000" in rows[0].old_value and "3000" in rows[0].new_value, \
            (rows[0].old_value, rows[0].new_value)
        # именно рубли, а не 5000000000
        assert "000000" not in rows[0].new_value, rows[0].new_value
    finally:
        db.close()


def test_unchanged_objects_produce_nothing():
    """Повторная сверка без правок не должна писать в журнал."""
    db = SessionLocal()
    try:
        dom = "quiet.example"
        ch.sync_object_type(db, dom, "campaign", [_campaign()])
        db.commit()
        ch.sync_object_type(db, dom, "campaign", [_campaign()])
        ch.sync_object_type(db, dom, "campaign", [_campaign()])
        db.commit()
        assert ch.recent(db, dom) == []
    finally:
        db.close()


def test_state_is_human_readable():
    """ON/SUSPENDED — не то, что нужно показывать человеку."""
    db = SessionLocal()
    try:
        dom = "state.example"
        ch.sync_object_type(db, dom, "campaign", [_campaign(state="ON")])
        db.commit()
        ch.sync_object_type(db, dom, "campaign", [_campaign(state="SUSPENDED")])
        db.commit()
        row = [c for c in ch.recent(db, dom) if c.field == "State"][0]
        assert row.old_value == "включена" and row.new_value == "остановлена", \
            (row.old_value, row.new_value)
    finally:
        db.close()


def test_new_and_removed_campaigns():
    db = SessionLocal()
    try:
        dom = "life.example"
        ch.sync_object_type(db, dom, "campaign", [_campaign()])
        db.commit()
        ch.sync_object_type(db, dom, "campaign", [_campaign(), _campaign("999", "Новая")])
        db.commit()
        added = [c for c in ch.recent(db, dom) if c.kind == "added"]
        assert len(added) == 1 and added[0].object_name == "Новая", added

        ch.sync_object_type(db, dom, "campaign", [_campaign()])
        db.commit()
        removed = [c for c in ch.recent(db, dom) if c.kind == "removed"]
        assert len(removed) == 1 and removed[0].object_name == "Новая", removed
    finally:
        db.close()


def test_bid_modifier_percent():
    """Корректировка — проценты, а не микроединицы."""
    db = SessionLocal()
    try:
        dom = "bm.example"
        rows_a = [{"Id": "1", "CampaignId": "111", "Type": "MOBILE_ADJUSTMENT",
                   "MobileAdjustment": {"BidModifier": 120}}]
        rows_b = [{"Id": "1", "CampaignId": "111", "Type": "MOBILE_ADJUSTMENT",
                   "MobileAdjustment": {"BidModifier": 80}}]
        ch.sync_object_type(db, dom, "bid_modifier", rows_a)
        db.commit()
        ch.sync_object_type(db, dom, "bid_modifier", rows_b)
        db.commit()
        row = ch.recent(db, dom)[0]
        assert row.old_value == "120%" and row.new_value == "80%", (row.old_value, row.new_value)
    finally:
        db.close()


def test_keyword_bid_in_rubles():
    db = SessionLocal()
    try:
        dom = "kb.example"
        ch.sync_object_type(db, dom, "keyword_bid",
                            [{"KeywordId": "7", "Bid": 35_000_000, "ContextBid": 10_000_000}])
        db.commit()
        ch.sync_object_type(db, dom, "keyword_bid",
                            [{"KeywordId": "7", "Bid": 42_500_000, "ContextBid": 10_000_000}])
        db.commit()
        row = [c for c in ch.recent(db, dom) if c.field == "Bid"][0]
        assert row.old_value == "35 ₽" and row.new_value == "42.50 ₽", \
            (row.old_value, row.new_value)
    finally:
        db.close()


def test_sync_survives_a_failing_service():
    """Ошибка одного сервиса не должна лишать журнала по остальным."""
    from app.providers.yandex_direct import DirectError, YandexDirectProvider

    db = SessionLocal()
    try:
        dom = "partial.example"
        YandexDirectProvider.get_campaigns = lambda self, d: [_campaign()]
        def _boom(self, d):
            raise DirectError("не принято имя поля")
        YandexDirectProvider.get_bid_modifiers = _boom
        ch.sync(db, dom, ["campaign", "bid_modifier"])
        db.commit()
        # кампания записана в снапшот, несмотря на падение корректировок
        from app.db.models_direct import DirectSettingsSnapshot
        from sqlalchemy import select
        n = db.execute(select(DirectSettingsSnapshot).where(
            DirectSettingsSnapshot.domain == dom)).scalars().all()
        assert len(n) == 1, n
    finally:
        db.close()


if __name__ == "__main__":
    setup_module()
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok   {name}")
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"  FAIL {name}: {type(exc).__name__}: {exc}")
    print("ПРОВАЛЕНО:" if failed else "Все тесты прошли.", failed or "")
    raise SystemExit(1 if failed else 0)
