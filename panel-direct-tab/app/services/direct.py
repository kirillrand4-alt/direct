"""Статистика Яндекс Директа для вкладки панели (живой запрос, по домену).

Тонкий слой над провайдером: тянет отчёты по кампаниям и по дням, приводит типы
(Директ отдаёт TSV со строками; пустые числа — как ``--``), считает производные
метрики (CTR, цена клика, цена конверсии) и итоги. Всё для одного домена за один
период; ошибки провайдера превращаются в поле ``error`` для показа на вкладке.
"""
from __future__ import annotations

import logging

from app.providers.yandex_direct import DirectError, YandexDirectProvider

logger = logging.getLogger(__name__)


def _f(value) -> float:
    """Терпимый float: ``None``/``""``/``"--"`` → 0.0; запятая как разделитель тоже ок."""
    s = str(value).strip() if value is not None else ""
    if s in ("", "--"):
        return 0.0
    try:
        return float(s.replace(" ", "").replace(",", "."))
    except ValueError:
        return 0.0


def _i(value) -> int:
    return int(round(_f(value)))


def _goals_for(domain) -> list[str]:
    """ID целей Метрики для конверсий: на домен или общие. Пусто = без конверсий."""
    from app.credentials import get_cred

    raw = ""
    if domain:
        raw = get_cred(f"direct_goals:{domain}") or ""
    raw = raw or get_cred("direct_goals") or ""
    return [g.strip() for g in raw.replace(";", ",").split(",") if g.strip()]


def _attribution_for(domain) -> list[str] | None:
    """Модель атрибуции конверсий: на домен или общая. Пусто = по умолчанию."""
    from app.credentials import get_cred

    raw = ""
    if domain:
        raw = get_cred(f"direct_attribution:{domain}") or ""
    raw = (raw or get_cred("direct_attribution") or "").strip().upper()
    return [raw] if raw in ("LC", "FC", "LSC", "LYDC", "AUTO") else None


def _empty(connected: bool, error: str | None = None, has_conversions: bool = False) -> dict:
    return {
        "connected": connected,
        "error": error,
        "totals": None,
        "daily": [],
        "campaigns": [],
        "has_conversions": has_conversions,
    }


def direct_overview(domain, dr) -> dict:
    """Собирает данные вкладки Директа для домена за период ``dr``.

    Возвращает: ``connected`` (задан ли токен), ``error`` (текст или None),
    ``totals`` (dict итогов или None), ``daily`` (список по дням),
    ``campaigns`` (список по кампаниям), ``has_conversions`` (есть ли цели).
    """
    provider = YandexDirectProvider()
    if not provider.is_connected(domain):
        return _empty(connected=False)

    goals = _goals_for(domain)
    # Колонки конверсий показываем и без явно заданных целей: Директ отдаёт
    # конверсии по всем целям кампании, и прятать их только потому, что цель не
    # выбрана вручную, — значит терять главную цифру отчёта. Окончательное
    # решение принимается ниже, по тому, пришло ли хоть что-то.
    has_conv = bool(goals)
    # Та же модель атрибуции, что и у сбора истории. Иначе на одной странице
    # живые конверсии и конверсии из истории считались бы по разным методикам
    # и не сходились бы между собой.
    attribution = _attribution_for(domain) if goals else None

    try:
        camp_rows = provider.campaigns(domain, dr, goals=goals, attribution=attribution)
        daily_rows = provider.daily(domain, dr, goals=goals, attribution=attribution)
    except DirectError as exc:
        return _empty(connected=True, error=str(exc), has_conversions=has_conv)
    except Exception as exc:  # noqa: BLE001 — на вкладке лучше показать текст, чем 500
        logger.exception("Direct overview failed for %s", domain)
        return _empty(connected=True, error=f"Непредвиденная ошибка: {exc}", has_conversions=has_conv)

    campaigns = []
    for r in camp_rows:
        impressions = _i(r.get("Impressions"))
        clicks = _i(r.get("Clicks"))
        cost = round(_f(r.get("Cost")), 2)
        conv = _i(r.get("Conversions")) if has_conv else 0
        campaigns.append({
            "id": r.get("CampaignId"),
            "name": r.get("CampaignName") or r.get("CampaignId") or "—",
            "impressions": impressions,
            "clicks": clicks,
            "cost": cost,
            "conversions": conv,
            "ctr": (clicks / impressions) if impressions else 0.0,
            "cpc": (cost / clicks) if clicks else 0.0,
        })
    campaigns.sort(key=lambda c: c["cost"], reverse=True)

    daily = []
    for r in daily_rows:
        daily.append({
            "date": r.get("Date"),
            "impressions": _i(r.get("Impressions")),
            "clicks": _i(r.get("Clicks")),
            "cost": round(_f(r.get("Cost")), 2),
            "conversions": _i(r.get("Conversions")) if has_conv else 0,
        })
    daily.sort(key=lambda d: d["date"] or "")

    ti = sum(c["impressions"] for c in campaigns)
    tc = sum(c["clicks"] for c in campaigns)
    tcost = round(sum(c["cost"] for c in campaigns), 2)
    tconv = sum(c["conversions"] for c in campaigns)
    totals = {
        "impressions": ti,
        "clicks": tc,
        "cost": tcost,
        "conversions": tconv,
        "ctr": (tc / ti) if ti else 0.0,
        "cpc": (tcost / tc) if tc else 0.0,
        "cpa": (tcost / tconv) if tconv else 0.0,
    }

    return {
        "connected": True,
        "error": None,
        "totals": totals,
        "daily": daily,
        "campaigns": campaigns,
        "has_conversions": has_conv or tconv > 0,
    }
