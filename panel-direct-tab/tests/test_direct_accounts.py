"""Разведка кабинетов Директа и массовая привязка их к доменам.

Ответы API подменяются: проверяется логика разбора списка, опознания домена и
выбора кабинета при дубле, а не доступность Директа.
"""
from __future__ import annotations

import os
import sys
import tempfile
from collections import Counter

os.environ["DATABASE_URL"] = "sqlite:///" + tempfile.mktemp(suffix=".db")
os.environ.setdefault("ENABLE_SCHEDULER", "false")
sys.path.insert(0, os.getcwd())

from app.db.base import Base, SessionLocal, engine  # noqa: E402
from app.db import models, models_direct  # noqa: E402,F401
from app.providers.yandex_direct import DirectError  # noqa: E402
from app.services import direct_accounts as acc  # noqa: E402

# Домены «панели» в тестах. У каждого теста, который пишет в общую БД, свой
# домен: база одна на файл, и пересечение доменов связывало бы тесты между собой.
PANEL = {"brand-a.example", "brand-b.example", "brand-c.example",
         "alias.example"}


def setup_module(_=None):
    Base.metadata.create_all(engine)


class FakeProvider:
    """Провайдер-заглушка: отдаёт заранее описанные кабинеты по Client-Login."""

    ADS_CAMPAIGN_CHUNK = 10

    def __init__(self, cabinets: dict):
        self.cabinets = cabinets
        self.ad_calls = 0

    def _cab(self, login):
        cab = self.cabinets.get(login)
        if cab is None:
            raise DirectError("Директ отклонил запрос: Объект не найден | код 8800")
        return cab

    def get_client_info(self, login=None, domain=None):
        cab = self._cab(login)
        return {"Login": login, "ClientId": cab["client_id"], "ClientInfo": cab["info"]}

    def list_campaigns_brief(self, login=None, domain=None):
        return list(self._cab(login)["campaigns"])

    def account_totals(self, dr, login=None, domain=None):
        cab = self._cab(login)
        return {"clicks": cab.get("clicks", 0), "cost": cab.get("cost", 0.0)}

    def ad_hrefs(self, campaign_ids, login=None, domain=None):
        self.ad_calls += 1
        cab = self._cab(login)
        ids = {str(c) for c in campaign_ids}
        return [h for cid, h in cab["hrefs"] if str(cid) in ids]


def _campaign(cid, state="ON", name="Поиск"):
    return {"Id": cid, "Name": name, "State": state}


# --------------------------- разбор списка логинов --------------------------- #

def test_parse_logins_drops_display_names():
    """Из интерфейса список копируется парами «логин / подпись» — подписи лишние."""
    pasted = """
    brand-a-100001-k7x2
    Бренд A · Редактор
    brand-b-100002-m3q8
    Бренд B · Редактор
    auto-a1b2c3d4
    brand-e-000001 · Администратор
    owner-login
    Основной кабинет · Владелец
    """
    assert acc.parse_logins(pasted) == [
        "brand-a-100001-k7x2", "brand-b-100002-m3q8", "auto-a1b2c3d4", "owner-login",
    ]


def test_parse_logins_dedupes_and_accepts_separators():
    assert acc.parse_logins("a-1, b-2; a-1\nb-2") == ["a-1", "b-2"]


def test_parse_logins_empty():
    assert acc.parse_logins("") == []
    assert acc.parse_logins("Основной кабинет · Владелец") == []


# ------------------------------ опознание домена ----------------------------- #

def test_host_of_strips_www_and_port():
    assert acc.host_of("https://WWW.Brand-A.example:443/catalog/") == "brand-a.example"
    assert acc.host_of("не ссылка") is None


def test_pick_domain_prefers_panel_domain_over_quiz_service():
    """Квиз-кампания ссылается на конструктор форм: по чистому большинству
    кабинет опознался бы как quiz-service.example, а не как сайт клиента."""
    hosts = Counter({"quiz-service.example": 90, "brand-a.example": 12})
    assert acc.pick_domain(hosts, PANEL) == "brand-a.example"


def test_pick_domain_falls_back_to_top_host_outside_panel():
    """Кабинет рекламирует сайт, которого в панели нет — это надо показать,
    а не молча отдать None."""
    hosts = Counter({"brand-d.example": 14, "quiz-service.example": 2})
    assert acc.pick_domain(hosts, PANEL) == "brand-d.example"


def test_pick_domain_without_hrefs():
    assert acc.pick_domain(Counter(), PANEL) is None


# ------------------------------- опрос кабинета ------------------------------ #

def test_probe_reads_cabinet_and_domain():
    provider = FakeProvider({"brand-a-100001-k7x2": {
        "client_id": "10000001", "info": "Бренд A",
        "campaigns": [_campaign(1), _campaign(2, "ARCHIVED")],
        "hrefs": [(1, "https://brand-a.example/catalog/"), (2, "https://old.example/")],
    }})
    row = acc.probe(provider, "brand-a-100001-k7x2", PANEL)
    assert row["status"] == "ok"
    assert row["client_id"] == "10000001"
    assert row["campaigns"] == 2 and row["live_campaigns"] == 1
    assert row["domain"] == "brand-a.example"


def test_probe_no_access_is_recorded_not_raised():
    """Кабинет без прав не должен ронять разведку остальных."""
    row = acc.probe(FakeProvider({}), "brand-c-100004-p1w5", PANEL)
    assert row["status"] == "error"
    assert "8800" in row["error_text"]
    assert row["domain"] is None


def test_probe_live_campaigns_sampled_first():
    """Домен берём из живых кампаний: архив мог вести на прежний сайт."""
    provider = FakeProvider({"x": {
        "client_id": "1", "info": "X",
        "campaigns": [_campaign(i, "ARCHIVED") for i in range(1, 30)] + [_campaign(99, "ON")],
        "hrefs": [(99, "https://brand-b.example/a")] * acc.ENOUGH_HREFS
                 + [(i, "https://staryi-sait.example/") for i in range(1, 30)],
    }})
    row = acc.probe(provider, "x", PANEL)
    assert row["domain"] == "brand-b.example"
    # хватило одной страницы — по всем архивным кампаниям не ходили
    assert provider.ad_calls == 1


# --------------------------- сохранение и привязка --------------------------- #

def _scan_duplicate_pair(db):
    """Два кабинета на один домен: рабочий и пустой дубль.

    Случай с боевого сервера: у обоих ровно по одной живой кампании, отличает
    их только расход. Логин дубля раньше по алфавиту — если бы тай-брейк шёл по
    имени, привязка досталась бы пустому кабинету.
    """
    provider = FakeProvider({
        "auto-e5f6g7h8": {"client_id": "10000006", "info": "brand-c-000002",
                          "campaigns": [_campaign(10)], "clicks": 3700, "cost": 190000.0,
                          "hrefs": [(10, "https://brand-c.example/x")] * 5},
        "dup-login": {"client_id": "10000007", "info": "brand-c дубль",
                             "campaigns": [_campaign(20)], "clicks": 0, "cost": 0.0,
                             "hrefs": [(20, "https://brand-c.example/y")] * 5},
    })
    return acc.scan(db, ["auto-e5f6g7h8", "dup-login"], PANEL, provider)


def test_scan_saves_rows_and_is_idempotent():
    db = SessionLocal()
    try:
        _scan_duplicate_pair(db)
        again = _scan_duplicate_pair(db)
        assert len(again) == 2
        assert len(acc.listing(db)) == 2  # повтор не плодит строки
    finally:
        db.close()


def test_suggest_picks_cabinet_with_spend_not_alphabet():
    """Кампаний поровну — выигрывает тот, через который идут деньги."""
    db = SessionLocal()
    try:
        _scan_duplicate_pair(db)
        best = acc.suggest(db, PANEL)
        assert best["brand-c.example"].login == "auto-e5f6g7h8"
        # и в таблице на вкладке рабочий кабинет стоит выше дубля
        order = [a.login for a in acc.listing(db) if a.domain == "brand-c.example"]
        assert order[0] == "auto-e5f6g7h8"
    finally:
        db.close()


def test_suggest_falls_back_to_campaigns_without_spend():
    """Оба кабинета без расхода — тогда решает число живых кампаний."""
    db = SessionLocal()
    try:
        provider = FakeProvider({
            "quiet-old": {"client_id": "1", "info": "старый",
                          "campaigns": [_campaign(1, "ARCHIVED")],
                          "hrefs": [(1, "https://brand-b.example/a")] * 3},
            "quiet-new": {"client_id": "2", "info": "новый",
                          "campaigns": [_campaign(2), _campaign(3)],
                          "hrefs": [(2, "https://brand-b.example/b")] * 3},
        })
        acc.scan(db, ["quiet-old", "quiet-new"], PANEL, provider)
        assert acc.suggest(db, PANEL)["brand-b.example"].login == "quiet-new"
    finally:
        db.close()


def test_probe_skips_spend_report_for_empty_cabinet():
    """У кабинета без кампаний отчёт не заказываем — это лишний вызов API."""
    calls = []

    class Counted(FakeProvider):
        def account_totals(self, dr, login=None, domain=None):
            calls.append(login)
            return super().account_totals(dr, login=login, domain=domain)

    provider = Counted({"owner-login": {
        "client_id": "10000009", "info": "Основной кабинет",
        "campaigns": [], "hrefs": []}})
    row = acc.probe(provider, "owner-login", PANEL)
    assert row["status"] == "ok" and row["campaigns"] == 0
    assert row["cost_90d"] == 0.0
    assert calls == []


def test_bind_all_writes_creds_and_spares_manual(monkeypatch):
    db = SessionLocal()
    try:
        _scan_duplicate_pair(db)
        written = {}
        monkeypatch.setattr("app.credentials.set_cred",
                            lambda k, v: written.__setitem__(k, v))
        monkeypatch.setattr("app.credentials.get_cred", lambda k, default=None: default)
        done = acc.bind_suggested(db, PANEL)
        assert done == [("brand-c.example", "auto-e5f6g7h8")]
        assert written["direct_login:brand-c.example"] == "auto-e5f6g7h8"

        # ручная привязка важнее результата разведки
        written.clear()
        monkeypatch.setattr(
            "app.credentials.get_cred",
            lambda k, default=None: ("ручной-логин"
                                     if k == "direct_login:brand-c.example" else default))
        assert acc.bind_suggested(db, PANEL) == []
        assert written == {}
        # …но по явному требованию перезаписывается
        assert acc.bind_suggested(db, PANEL, overwrite=True) == [
            ("brand-c.example", "auto-e5f6g7h8")]
    finally:
        db.close()


def test_bind_all_keeps_alias_of_the_same_cabinet(monkeypatch):
    """Один кабинет под двумя логинами — привязка не должна прыгать между ними.

    Случай с боевого сервера: основной кабинет доступен как main-login и
    как main-100003-r4v7, ClientId у обоих 10000003 и данные одни и те
    же. Победитель среди равных решался алфавитом, то есть каждая разведка
    переписывала привязку без всякой причины.
    """
    db = SessionLocal()
    try:
        cab = {"client_id": "10000003", "info": "Основной кабинет",
               "campaigns": [_campaign(1)], "clicks": 100000, "cost": 5000000.0,
               "hrefs": [(1, "https://alias.example/p")] * 5}
        acc.scan(db, ["main-login", "main-100003-r4v7"], PANEL,
                 FakeProvider({"main-login": cab, "main-100003-r4v7": cab}))
        written = {}
        monkeypatch.setattr("app.credentials.set_cred",
                            lambda k, v: written.__setitem__(k, v))
        monkeypatch.setattr(
            "app.credentials.get_cred",
            lambda k, default=None: ("main-login"
                                     if k == "direct_login:alias.example" else default))
        assert acc.bind_suggested(db, PANEL, overwrite=True) == []
        assert written == {}
    finally:
        db.close()


def test_suggest_ignores_domains_outside_panel():
    db = SessionLocal()
    try:
        provider = FakeProvider({"brand-d-100005-t6z9": {
            "client_id": "10000010", "info": "Бренд D",
            "campaigns": [_campaign(1)],
            "hrefs": [(1, "https://brand-d.example/z")] * 3}})
        acc.scan(db, ["brand-d-100005-t6z9"], PANEL, provider)
        assert "brand-d.example" not in acc.suggest(db, PANEL)
        # но в списке кабинетов он виден — с пометкой «нет в панели» на вкладке
        assert any(a.login == "brand-d-100005-t6z9" and a.domain == "brand-d.example"
                   for a in acc.listing(db))
    finally:
        db.close()
