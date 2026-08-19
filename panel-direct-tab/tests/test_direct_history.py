"""Сквозная проверка истории Директа на копии панели.

Провайдер подменяется заглушкой — тест проверяет наш код (разбор TSV-строк,
идемпотентный upsert, агрегаты, ночной прогон, рендер), а не доступность API
Яндекса.

Запуск из корня панели с установленным пакетом:
    python -m pytest tests/test_direct_history.py -q
либо напрямую:  python tests/test_direct_history.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, timedelta

# ВАЖНО: настройка называется database_url → переменная DATABASE_URL.
# С неверным именем тесты писали бы в боевую БД панели и копили состояние.
os.environ["DATABASE_URL"] = "sqlite:///" + tempfile.mktemp(suffix=".db")
os.environ.setdefault("ENABLE_SCHEDULER", "false")
sys.path.insert(0, os.getcwd())

from app.db.base import Base, SessionLocal, engine  # noqa: E402
from app.db import models  # noqa: E402,F401 — таблицы самой панели
from app.db import models_direct  # noqa: E402,F401 — таблицы Директа
from app.services import direct_store as store  # noqa: E402

D = "example.com"
DAY = date(2026, 8, 1)


def _rows(day: date, clicks: int, cost: str, campaign="111"):
    return [{
        "Date": day.isoformat(), "CampaignId": campaign, "CampaignName": "Поиск — Москва",
        "Impressions": "1000", "Clicks": str(clicks), "Cost": cost, "Conversions": "3",
    }]


def setup_module(_=None):
    Base.metadata.create_all(engine)


def test_schema_created():
    names = set(Base.metadata.tables)
    for t in ("direct_daily", "direct_campaign_daily", "direct_breakdown_daily",
              "direct_collect_run"):
        assert t in names, f"нет таблицы {t}"


def test_upsert_is_idempotent_and_overwrites():
    """Повторный сбор того же дня перезаписывает строку, а не дублирует её —
    без этого правки Директа задним числом ломали бы историю."""
    db = SessionLocal()
    try:
        store.save_daily(db, D, "", _rows(DAY, 10, "100.50"))
        store.save_daily(db, D, "", _rows(DAY, 10, "100.50"))
        db.commit()
        t = store.totals(db, D, DAY, DAY)
        assert t["clicks"] == 10, t
        assert abs(t["cost"] - 100.50) < 1e-6, t

        # Директ пересчитал день — цифры должны замениться, а не сложиться
        store.save_daily(db, D, "", _rows(DAY, 8, "80.25"))
        db.commit()
        t = store.totals(db, D, DAY, DAY)
        assert t["clicks"] == 8, t
        assert abs(t["cost"] - 80.25) < 1e-6, t
    finally:
        db.close()


def test_tsv_quirks_parsed():
    """'--' вместо пустого, запятая как разделитель дробной части, неразрывный
    пробел в тысячах — всё это реально приходит в TSV Директа."""
    db = SessionLocal()
    try:
        d = DAY + timedelta(days=1)
        store.save_daily(db, D, "", [{
            "Date": d.isoformat(), "Impressions": "1\xa0234", "Clicks": "12",
            "Cost": "1234,56", "Conversions": "--",
        }])
        db.commit()
        t = store.totals(db, D, d, d)
        assert t["impressions"] == 1234, t
        assert abs(t["cost"] - 1234.56) < 1e-6, t
        assert t["conversions"] == 0, t
    finally:
        db.close()


def test_attribution_keeps_histories_apart():
    """Смена модели атрибуции не должна затирать уже собранные числа."""
    db = SessionLocal()
    try:
        d = DAY + timedelta(days=2)
        store.save_daily(db, D, "", _rows(d, 5, "50"))
        store.save_daily(db, D, "LSC", _rows(d, 7, "70"))
        db.commit()
        assert store.totals(db, D, d, d, "")["clicks"] == 5
        assert store.totals(db, D, d, d, "LSC")["clicks"] == 7
    finally:
        db.close()


def test_breakdown_merges_duplicate_keys():
    """Одна фраза приходит несколькими строками (разные группы) — в истории
    должна остаться одна строка с суммой."""
    db = SessionLocal()
    try:
        d = DAY + timedelta(days=3)
        raw = [
            {"Date": d.isoformat(), "CampaignId": "1", "CriterionId": "77",
             "Criterion": "купить компрессор", "Impressions": "100", "Clicks": "5", "Cost": "50"},
            {"Date": d.isoformat(), "CampaignId": "2", "CriterionId": "77",
             "Criterion": "купить компрессор", "Impressions": "40", "Clicks": "3", "Cost": "30"},
        ]
        store.save_breakdown(db, D, "criteria", "", raw, "CriterionId", "Criterion")
        db.commit()
        rows = store.breakdown(db, D, "criteria", d, d)
        assert len(rows) == 1, rows
        assert rows[0]["clicks"] == 8 and abs(rows[0]["cost"] - 80) < 1e-6, rows
    finally:
        db.close()


def test_renamed_campaign_shows_newest_name():
    """После переименования в таблице должно быть свежее имя.

    Регрессия: раньше имя бралось как ``max(campaign_name)`` в агрегате, а это
    лексикографический максимум — «Поиск — Москва» больше «Поиск — МСК», и
    показывалось старое название.
    """
    db = SessionLocal()
    try:
        dom, d1 = "rename.example", date(2026, 9, 1)
        d2 = d1 + timedelta(days=1)
        store.save_campaigns(db, dom, "", [{
            "Date": d1.isoformat(), "CampaignId": "111", "CampaignName": "Поиск — Москва",
            "Impressions": "100", "Clicks": "10", "Cost": "100"}])
        store.save_campaigns(db, dom, "", [{
            "Date": d2.isoformat(), "CampaignId": "111", "CampaignName": "Поиск — МСК",
            "Impressions": "100", "Clicks": "10", "Cost": "100"}])
        db.commit()
        rows = store.campaigns(db, dom, d1, d2)
        assert rows[0]["campaign_name"] == "Поиск — МСК", rows[0]["campaign_name"]
        # суммы при этом должны остаться по всей кампании, а не по одному дню
        assert rows[0]["clicks"] == 20, rows[0]
    finally:
        db.close()


def test_campaign_without_name_keeps_previous():
    """Строка без названия не должна затирать нормальное имя."""
    db = SessionLocal()
    try:
        dom, d1 = "noname.example", date(2026, 9, 10)
        d2 = d1 + timedelta(days=1)
        store.save_campaigns(db, dom, "", [{
            "Date": d1.isoformat(), "CampaignId": "222", "CampaignName": "РСЯ — Россия",
            "Impressions": "10", "Clicks": "1", "Cost": "10"}])
        store.save_campaigns(db, dom, "", [{
            "Date": d2.isoformat(), "CampaignId": "222", "CampaignName": "",
            "Impressions": "10", "Clicks": "1", "Cost": "10"}])
        db.commit()
        assert store.campaigns(db, dom, d1, d2)[0]["campaign_name"] == "РСЯ — Россия"
    finally:
        db.close()


def test_window_widens_for_late_revisions():
    """Окно очередного сбора должно заходить назад на REFETCH_DAYS от последнего
    успешного дня — Директ правит свежие цифры несколько суток."""
    from app.db.models_direct import DirectCollectRun
    from app.services import direct_collect

    db = SessionLocal()
    try:
        today = date(2026, 8, 20)
        # истории нет — берём широкое стартовое окно
        w = direct_collect.compute_window(db, "fresh.example", today)
        assert w.end == today - timedelta(days=1)
        assert (w.end - w.start).days == direct_collect.SEED_DAYS

        db.add(DirectCollectRun(domain=D, job_type="daily", status="ok",
                                target_date=date(2026, 8, 18)))
        db.commit()
        w = direct_collect.compute_window(db, D, today)
        assert w.start == date(2026, 8, 18) - timedelta(days=direct_collect.REFETCH_DAYS - 1), w.start
        assert w.end == date(2026, 8, 19), w.end
    finally:
        db.close()


def test_daily_run_survives_a_broken_domain(monkeypatch=None):
    """Ошибка Директа по одному домену не должна ронять прогон."""
    from app.providers.yandex_direct import DirectError
    from app.services import direct_collect

    db = SessionLocal()
    # подмены обязательно откатываем: без этого соседние тесты видят заглушки
    orig_domains = direct_collect.connected_domains
    orig_collect = direct_collect.collect
    try:
        direct_collect.connected_domains = lambda _db: ["bad.example"]
        def _boom(*a, **k):
            raise DirectError("нет доступа к API")
        direct_collect.collect = _boom
        res = direct_collect.run_daily(db)
        assert res == {"bad.example": -1}, res
    finally:
        direct_collect.connected_domains = orig_domains
        direct_collect.collect = orig_collect
        db.close()


def test_page_renders_with_history():
    """Страница должна отрисоваться и с историей, и без неё.

    Путь берём из настроек, а не зашиваем: на боевом сервере панель живёт под
    префиксом (``base_path=/stat``), и запрос к ``/direct`` там отдаёт 404 —
    тест падал на окружении, а не на коде.
    """
    from fastapi.testclient import TestClient
    from app.config import get_settings
    from app.main import create_app

    client = TestClient(create_app())
    r = client.get(f"{get_settings().base_path}/direct")
    assert r.status_code == 200, r.status_code
    assert "Директ" in r.text


def test_general_token_connects_only_its_own_domain():
    """Общий токен не должен «подключать» все домены панели.

    Регрессия с боевого сервера: общего токена хватало, чтобы is_connected()
    вернул True для любого домена. В панели 28 доменов — статистика одного
    кабинета записалась 28 раз под разными именами, а ночной сбор сходил
    в API 28 раз вместо одного.
    """
    from app.credentials import set_cred
    from app.providers.yandex_direct import YandexDirectProvider

    p = YandexDirectProvider()
    set_cred("yandex_direct_token", "y0_OBSHCHIY")
    set_cred("direct_main_domain", "")
    # общий токен есть, владелец не назначен — не угадываем
    assert p.is_connected("brand-b.example") is False
    assert p.is_connected("brand-f.example") is False

    set_cred("direct_main_domain", "brand-b.example")
    assert p.is_connected("brand-b.example") is True
    assert p.is_connected("brand-f.example") is False

    # у своего токена приоритет: домен подключён независимо от привязки общего
    set_cred("direct_token:brand-f.example", "y0_SVOY")
    assert p.is_connected("brand-f.example") is True

    # агентский случай: свой Client-Login тоже считается привязкой
    set_cred("direct_login:brand-h.example", "own-login")
    assert p.is_connected("brand-h.example") is True
    assert p.is_connected("brand-g.example") is False


def test_only_bound_domains_are_collected():
    """connected_domains() возвращает только явно привязанные домены."""
    from app.credentials import set_cred
    from app.services import direct_collect

    set_cred("yandex_direct_token", "y0_OBSHCHIY")
    set_cred("direct_main_domain", "brand-b.example")

    import app.api.routes_pages as rp
    rp._domains = lambda db: [{"domain": d, "engines": ["gsc"]} for d in
                              ("brand-b.example", "brand-f.example", "brand-g.example", "brand-h.example")]
    db = SessionLocal()
    try:
        got = sorted(direct_collect.connected_domains(db))
        # brand-f и brand-h привязаны в предыдущем тесте (свой токен / свой логин)
        assert "brand-b.example" in got, got
        assert "brand-g.example" not in got, got
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
