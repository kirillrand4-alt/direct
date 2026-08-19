"""Тесты вкладки Директа: провайдер (Reports API) и сервис-агрегатор.

HTTP замокан через respx (уже в requirements панели). Креды подменяются
монипатчем ``app.credentials.get_cred``. Запуск: ``pytest tests/test_direct.py``.
"""
from __future__ import annotations

import json
from datetime import date

import httpx
import pytest
import respx

from app.providers.base import DateRange
from app.providers.yandex_direct import REPORTS_URL, DirectError, YandexDirectProvider
from app.services.direct import direct_overview

TSV_CAMPAIGNS = (
    "CampaignId\tCampaignName\tImpressions\tClicks\tCost\n"
    "111\tПоиск — Москва\t8449\t141\t3150.50\n"
    "222\tРСЯ — вся Россия\t2000\t30\t900.00\n"
)
TSV_DAILY = (
    "Date\tImpressions\tClicks\tCost\n"
    "2026-08-01\t4000\t70\t1500.00\n"
    "2026-08-02\t6449\t101\t2550.50\n"
)


def _dr():
    return DateRange(start=date(2026, 8, 1), end=date(2026, 8, 2))


def _creds(**kv):
    def fake_get_cred(key, default=None):
        return kv.get(key, default)
    return fake_get_cred


@respx.mock
def test_report_200_parses_tsv(monkeypatch):
    monkeypatch.setattr("app.credentials.get_cred", _creds(yandex_direct_token="y0_TEST", direct_main_domain="brand-c.example"))
    respx.post(REPORTS_URL).mock(return_value=httpx.Response(200, text=TSV_CAMPAIGNS))
    rows = YandexDirectProvider().campaigns("brand-c.example", _dr())
    assert rows[0]["CampaignName"] == "Поиск — Москва"
    assert rows[0]["Clicks"] == "141"


@respx.mock
def test_report_202_then_200(monkeypatch):
    """201/202 = очередь: повторяем тот же запрос, пока не 200."""
    monkeypatch.setattr("app.credentials.get_cred", _creds(yandex_direct_token="y0_TEST", direct_main_domain="brand-c.example"))
    monkeypatch.setattr("app.providers.yandex_direct.time.sleep", lambda *_: None)
    route = respx.post(REPORTS_URL)
    route.side_effect = [
        httpx.Response(202, headers={"retryIn": "0"}),
        httpx.Response(200, text=TSV_DAILY),
    ]
    rows = YandexDirectProvider().daily("brand-c.example", _dr())
    assert len(rows) == 2
    assert route.call_count == 2


@respx.mock
def test_money_not_in_micros_header(monkeypatch):
    """Провайдер обязан слать returnMoneyInMicros: false и Bearer-токен."""
    monkeypatch.setattr("app.credentials.get_cred", _creds(yandex_direct_token="y0_TEST", direct_main_domain="brand-c.example"))
    captured = {}

    def handler(request):
        captured["headers"] = request.headers
        return httpx.Response(200, text=TSV_CAMPAIGNS)

    respx.post(REPORTS_URL).mock(side_effect=handler)
    YandexDirectProvider().campaigns("d.ru", _dr())
    assert captured["headers"]["returnMoneyInMicros"] == "false"
    assert captured["headers"]["Authorization"] == "Bearer y0_TEST"


@respx.mock
def test_agency_client_login_sent(monkeypatch):
    monkeypatch.setattr(
        "app.credentials.get_cred",
        _creds(yandex_direct_token="y0_TEST", **{"direct_login:d.ru": "client-login"}),
    )
    captured = {}

    def handler(request):
        captured["login"] = request.headers.get("Client-Login")
        return httpx.Response(200, text=TSV_CAMPAIGNS)

    respx.post(REPORTS_URL).mock(side_effect=handler)
    YandexDirectProvider().campaigns("d.ru", _dr())
    assert captured["login"] == "client-login"


@respx.mock
def test_401_becomes_direct_error(monkeypatch):
    monkeypatch.setattr("app.credentials.get_cred", _creds(yandex_direct_token="bad"))
    respx.post(REPORTS_URL).mock(return_value=httpx.Response(401, text="unauthorized"))
    with pytest.raises(DirectError):
        YandexDirectProvider().campaigns("d.ru", _dr())


def test_overview_not_connected(monkeypatch):
    monkeypatch.setattr("app.credentials.get_cred", _creds())  # ни одного токена
    out = direct_overview("d.ru", _dr())
    assert out["connected"] is False
    assert out["totals"] is None


@respx.mock
def test_overview_aggregates(monkeypatch):
    monkeypatch.setattr("app.credentials.get_cred", _creds(yandex_direct_token="y0_TEST", direct_main_domain="brand-c.example"))
    respx.post(REPORTS_URL).mock(
        side_effect=[
            httpx.Response(200, text=TSV_CAMPAIGNS),  # campaigns()
            httpx.Response(200, text=TSV_DAILY),      # daily()
        ]
    )
    out = direct_overview("brand-c.example", _dr())
    assert out["connected"] is True and out["error"] is None
    t = out["totals"]
    assert t["impressions"] == 10449
    assert t["clicks"] == 171
    assert round(t["cost"], 2) == 4050.50
    # CTR = 171/10449, CPC = 4050.5/171
    assert abs(t["ctr"] - 171 / 10449) < 1e-9
    assert abs(t["cpc"] - 4050.50 / 171) < 1e-9
    # кампании отсортированы по расходу убыв.
    assert out["campaigns"][0]["name"] == "Поиск — Москва"
    assert out["has_conversions"] is False


@respx.mock
def test_overview_error_is_caught(monkeypatch):
    # домен теста — d.ru, к нему и должен быть привязан общий токен
    monkeypatch.setattr("app.credentials.get_cred",
                        _creds(yandex_direct_token="y0_TEST", direct_main_domain="d.ru"))
    respx.post(REPORTS_URL).mock(return_value=httpx.Response(400, json={"error": {
        "error_string": "Bad", "error_detail": "плохой период"}}))
    out = direct_overview("d.ru", _dr())
    assert out["connected"] is True
    assert "плохой период" in out["error"]
    assert out["totals"] is None


@respx.mock
def test_bid_modifiers_always_send_campaign_ids(monkeypatch):
    """SelectionCriteria без CampaignIds сервис не принимает (ошибка 4001).

    Регрессия с боевого сервера: запрос «только по уровням» делался первым,
    падал всегда и лишь потом шёл запасной путь — два лишних обращения к API
    на каждый домен каждую ночь.
    """
    monkeypatch.setattr("app.credentials.get_cred", _creds(yandex_direct_token="y0_TEST"))
    seen = []

    def handler(request):
        body = json.loads(request.content.decode("utf-8"))
        seen.append(body["params"]["SelectionCriteria"])
        return httpx.Response(200, json={"result": {"BidModifiers": []}})

    respx.post(REPORTS_URL.rsplit("/", 1)[0] + "/bidmodifiers").mock(side_effect=handler)
    YandexDirectProvider().get_bid_modifiers("d.ru", campaign_ids=["111", "222"])
    assert seen and all("CampaignIds" in s for s in seen), seen
    assert seen[0]["CampaignIds"] == ["111", "222"]


def test_bid_modifiers_skip_api_without_campaigns(monkeypatch):
    """У аккаунта без кампаний спрашивать корректировки не о чем."""
    monkeypatch.setattr("app.credentials.get_cred", _creds(yandex_direct_token="y0_TEST"))
    assert YandexDirectProvider().get_bid_modifiers("d.ru", campaign_ids=[]) == []


@respx.mock
def test_conversions_requested_without_goals(monkeypatch):
    """Конверсии должны запрашиваться всегда.

    Регрессия с боевого сервера: поле добавлялось только вместе с ``Goals``,
    целей никто не задавал — и панель хранила нули там, где у аккаунтов были
    десятки тысяч конверсий. Без ``Goals`` Директ отдаёт конверсии по всем
    целям кампании, ровно как показывает его интерфейс.
    """
    monkeypatch.setattr("app.credentials.get_cred",
                        _creds(yandex_direct_token="y0_TEST", direct_main_domain="d.ru"))
    captured = {}

    def handler(request):
        captured["params"] = json.loads(request.content.decode("utf-8"))["params"]
        return httpx.Response(200, text=TSV_CAMPAIGNS)

    respx.post(REPORTS_URL).mock(side_effect=handler)
    YandexDirectProvider().campaigns("d.ru", _dr())
    assert "Conversions" in captured["params"]["FieldNames"]
    # без целей модель атрибуции не уходит — Директ её отвергнет
    assert "Goals" not in captured["params"]
    assert "AttributionModels" not in captured["params"]


@respx.mock
def test_goals_still_narrow_the_report(monkeypatch):
    """Явно заданные цели по-прежнему сужают выборку и включают атрибуцию."""
    monkeypatch.setattr("app.credentials.get_cred",
                        _creds(yandex_direct_token="y0_TEST", direct_main_domain="d.ru"))
    captured = {}

    def handler(request):
        captured["params"] = json.loads(request.content.decode("utf-8"))["params"]
        return httpx.Response(200, text=TSV_CAMPAIGNS)

    respx.post(REPORTS_URL).mock(side_effect=handler)
    YandexDirectProvider().campaigns("d.ru", _dr(), goals=["123"], attribution=["LSC"])
    assert captured["params"]["Goals"] == ["123"]
    assert captured["params"]["AttributionModels"] == ["LSC"]
    assert captured["params"]["FieldNames"].count("Conversions") == 1
