"""Yandex Direct provider — рекламная статистика через сервис Reports (API v5).

Авторизация — один OAuth-токен (Bearer). Статистика берётся из сервиса Reports
(https://api.direct.yandex.com/json/v5/reports): POST с описанием отчёта, затем
опрос до готовности (HTTP 200 = готов, TSV в теле; 201/202 = поставлен в очередь,
повтор через заголовок ``retryIn``). Деньги запрашиваются в валюте
(``returnMoneyInMicros: false``), поэтому ``Cost`` уже в рублях.

Панель — по доменам, обычно один аккаунт Директа на домен. Токен хранится в
зашифрованном хранилище кредов панели (ключ ``yandex_direct_token``); при наличии
дополнительно читаются токен на домен (``direct_token:<домен>``) и агентский
``Client-Login`` (``direct_login:<домен>``).

HTTP — через ``httpx`` (как остальные провайдеры Яндекса в этом приложении).
"""
from __future__ import annotations

import logging
import time

import httpx

logger = logging.getLogger(__name__)

REPORTS_URL = "https://api.direct.yandex.com/json/v5/reports"
SANDBOX_URL = "https://api-sandbox.direct.yandex.com/json/v5/reports"

# Потолок опроса очереди офлайн-отчёта (число попыток) и пауза по умолчанию,
# если Директ не прислал заголовок retryIn.
_MAX_POLLS = 12
_DEFAULT_RETRY = 5
_MAX_SLEEP = 12  # не держим воркер дольше этого на одну паузу


class DirectError(RuntimeError):
    """Понятная человеку ошибка Директа — показывается прямо на вкладке."""


def _int(value, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _error_text(resp: httpx.Response) -> str:
    """Достаёт человекочитаемый текст ошибки из JSON-ответа Директа."""
    try:
        err = resp.json().get("error", {}) or {}
    except ValueError:
        return resp.text[:200]
    parts = [err.get("error_string"), err.get("error_detail")]
    code = err.get("error_code")
    if code:
        parts.append(f"код {code}")
    return " | ".join(p for p in parts if p) or resp.text[:200]


def _parse_tsv(text: str) -> list[dict]:
    """TSV-ответ Reports → список словарей {имя_поля: значение}."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []
    header = lines[0].split("\t")
    rows: list[dict] = []
    for ln in lines[1:]:
        cells = ln.split("\t")
        rows.append(dict(zip(header, cells)))
    return rows


class YandexDirectProvider:
    """Клиент сервиса Reports. Методы возвращают «сырые» строки отчёта (dict);
    приведение типов и агрегация — в ``app.services.direct``."""

    code = "yandex_direct"

    def __init__(self, sandbox: bool | None = None):
        self._sandbox = sandbox

    # ---------- креды ---------- #
    def _token(self, domain: str | None) -> str:
        from app.credentials import get_cred

        token = None
        if domain:
            token = get_cred(f"direct_token:{domain}")
        token = token or get_cred("yandex_direct_token")
        if not token or not token.strip():
            raise DirectError(
                "Не задан OAuth-токен Яндекс.Директа — вставьте его в настройках вкладки."
            )
        return token.strip()

    def _login(self, domain: str | None) -> str | None:
        from app.credentials import get_cred

        if domain:
            v = get_cred(f"direct_login:{domain}")
            if v and v.strip():
                return v.strip()
        return None

    def is_connected(self, domain: str | None) -> bool:
        from app.credentials import get_cred

        if domain and (get_cred(f"direct_token:{domain}") or "").strip():
            return True
        return bool((get_cred("yandex_direct_token") or "").strip())

    def _url(self) -> str:
        from app.credentials import get_cred

        sandbox = self._sandbox
        if sandbox is None:
            sandbox = (get_cred("direct_sandbox") or "").strip().lower() in ("1", "true", "yes")
        return SANDBOX_URL if sandbox else REPORTS_URL

    # ---------- запрос отчёта ---------- #
    def _report(self, domain, dr, report_type: str, field_names, goals=None,
                attribution=None) -> list[dict]:
        headers = {
            "Authorization": f"Bearer {self._token(domain)}",
            "Accept-Language": "ru",
            "Content-Type": "application/json; charset=utf-8",
            "processingMode": "auto",
            # без этого деньги вернутся умноженными на 1 000 000
            "returnMoneyInMicros": "false",
            "skipReportHeader": "true",
            "skipReportSummary": "true",
        }
        login = self._login(domain)
        if login:
            headers["Client-Login"] = login

        params: dict = {
            "SelectionCriteria": {
                "DateFrom": dr.start.isoformat(),
                "DateTo": dr.end.isoformat(),
            },
            "FieldNames": list(field_names),
            # имя обязано быть уникальным: Директ кеширует отчёты по имени и на повтор
            # с тем же именем, но другими параметрами отвечает ошибкой
            "ReportName": f"panel_{report_type}_{dr.start:%Y%m%d}_{dr.end:%Y%m%d}_{int(time.time() * 1000)}",
            "ReportType": report_type,
            "DateRangeType": "CUSTOM_DATE",
            "Format": "TSV",
            "IncludeVAT": "YES",
        }
        if goals:
            params["Goals"] = [str(g) for g in goals]
            # Модель атрибуции имеет смысл только вместе с целями: она меняет,
            # какому визиту засчитывается конверсия. Без Goals Директ её отвергнет.
            if attribution:
                params["AttributionModels"] = [str(a) for a in attribution]

        body = {"params": params}
        url = self._url()

        for _ in range(_MAX_POLLS):
            try:
                resp = httpx.post(url, json=body, headers=headers, timeout=120)
            except httpx.HTTPError as exc:
                raise DirectError(f"Директ недоступен: {exc}") from exc

            code = resp.status_code
            if code == 200:
                return _parse_tsv(resp.text)
            if code in (201, 202):
                retry_in = _int(resp.headers.get("retryIn"), _DEFAULT_RETRY)
                time.sleep(min(max(retry_in, 1), _MAX_SLEEP))
                continue
            if code == 400:
                raise DirectError(f"Директ отклонил запрос: {_error_text(resp)}")
            if code == 401:
                raise DirectError(
                    "Токен Директа не принят (истёк или без права «Использование API Яндекс Директа»)."
                )
            if code == 429:
                raise DirectError("Директ: превышены лимиты запросов, попробуйте позже.")
            raise DirectError(f"Директ HTTP {code}: {_error_text(resp)}")

        raise DirectError(
            "Отчёт Директа не готов за отведённое время — попробуйте позже или сузьте период."
        )

    # ---------- обычные сервисы API v5 (не Reports) ---------- #
    def _call(self, domain, service: str, method: str, params: dict) -> dict:
        """Вызов обычного сервиса v5: ``{"method": ..., "params": ...}`` → ``result``.

        Отличается от ``_report`` во всём: другой URL (сервис в пути), другое тело,
        ответ — JSON, а не TSV, и нет очереди с ``retryIn``. Поэтому отдельный метод,
        а не параметр к существующему.
        """
        url = self._url().rsplit("/", 1)[0] + f"/{service}"
        headers = {
            "Authorization": f"Bearer {self._token(domain)}",
            "Accept-Language": "ru",
            "Content-Type": "application/json; charset=utf-8",
        }
        login = self._login(domain)
        if login:
            headers["Client-Login"] = login

        try:
            resp = httpx.post(url, json={"method": method, "params": params},
                              headers=headers, timeout=120)
        except httpx.HTTPError as exc:
            raise DirectError(f"Директ недоступен: {exc}") from exc

        if resp.status_code != 200:
            raise DirectError(f"Директ HTTP {resp.status_code}: {_error_text(resp)}")
        try:
            payload = resp.json()
        except ValueError as exc:
            raise DirectError("Директ вернул не-JSON") from exc
        if "error" in payload:
            raise DirectError(f"Директ отклонил запрос: {_error_text(resp)}")
        return payload.get("result") or {}

    def _paged(self, domain, service: str, params: dict, key: str,
               limit: int = 1000) -> list[dict]:
        """``get`` с постраничным обходом: Директ отдаёт ``LimitedBy`` — смещение
        следующей страницы. Без обхода у крупного аккаунта молча потерялся бы хвост."""
        out: list[dict] = []
        offset = 0
        while True:
            page = dict(params)
            page["Page"] = {"Limit": limit, "Offset": offset}
            result = self._call(domain, service, "get", page)
            rows = result.get(key) or []
            out.extend(rows)
            limited_by = result.get("LimitedBy")
            if not rows or limited_by is None:
                break
            offset = int(limited_by)
        return out

    # Настройки кампании, которые имеет смысл сторожить. Только общие для всех
    # типов кампаний поля — типоспецифичные (BiddingStrategy) запрашиваются
    # отдельным списком и переживают отсутствие.
    CAMPAIGN_FIELDS = [
        "Id", "Name", "Type", "State", "Status", "StatusPayment",
        "StartDate", "EndDate", "Currency", "DailyBudget",
        "TimeTargeting", "NegativeKeywords", "BlockedIps", "ExcludedSites",
    ]

    def get_campaigns(self, domain) -> list[dict]:
        """Полные настройки всех кампаний аккаунта (для снапшота и сравнения)."""
        params = {
            "SelectionCriteria": {},
            "FieldNames": list(self.CAMPAIGN_FIELDS),
            "TextCampaignFieldNames": ["BiddingStrategy"],
        }
        try:
            return self._paged(domain, "campaigns", params, "Campaigns")
        except DirectError as exc:
            # Набор полей зависит от типов кампаний в аккаунте: если Директ не
            # принял типоспецифичный список — пробуем без него, чтобы не терять
            # всё остальное из-за одной стратегии.
            if "BiddingStrategy" not in str(exc):
                raise
            logger.warning("Директ: BiddingStrategy не принят (%s) — читаю без стратегии", exc)
            params.pop("TextCampaignFieldNames", None)
            return self._paged(domain, "campaigns", params, "Campaigns")

    def get_bid_modifiers(self, domain) -> list[dict]:
        """Корректировки ставок: мобильные, демография, регионы, ретаргетинг."""
        params = {
            "SelectionCriteria": {},
            "FieldNames": ["Id", "CampaignId", "AdGroupId", "Type"],
            "MobileAdjustmentFieldNames": ["BidModifier"],
            "DesktopAdjustmentFieldNames": ["BidModifier"],
            "DemographicsAdjustmentFieldNames": ["BidModifier", "Gender", "Age"],
            "RegionalAdjustmentFieldNames": ["BidModifier", "RegionId"],
            "RetargetingAdjustmentFieldNames": ["BidModifier", "RetargetingConditionId"],
        }
        return self._paged(domain, "bidmodifiers", params, "BidModifiers")

    def get_keyword_bids(self, domain) -> list[dict]:
        """Ставки по ключевым фразам.

        Объём: десятки тысяч строк на крупном аккаунте, поэтому включается
        отдельной галочкой и по умолчанию выключено.
        """
        params = {"SelectionCriteria": {}, "FieldNames": ["KeywordId", "CampaignId",
                                                          "AdGroupId", "Bid", "ContextBid"]}
        return self._paged(domain, "bids", params, "Bids")

    def changes_since(self, domain, timestamp: str) -> dict:
        """Что изменилось с момента ``timestamp`` (ISO 8601, UTC, вида
        ``2026-08-10T12:00:00Z``).

        Возвращает сырой ``result``: сервис отдаёт только ID изменившихся объектов
        и метку времени, без подробностей — что именно поменялось, приходится
        выяснять сравнением снапшотов.
        """
        return self._call(domain, "changes", "checkCampaigns", {"Timestamp": timestamp})

    # ---------- готовые отчёты (живой просмотр) ---------- #
    def campaigns(self, domain, dr, goals=None, attribution=None) -> list[dict]:
        """Итоги за период в разрезе кампаний."""
        fields = ["CampaignId", "CampaignName", "Impressions", "Clicks", "Cost"]
        if goals:
            fields.append("Conversions")
        return self._report(domain, dr, "CAMPAIGN_PERFORMANCE_REPORT", fields,
                            goals, attribution)

    def daily(self, domain, dr, goals=None, attribution=None) -> list[dict]:
        """Подённая динамика по всему аккаунту."""
        fields = ["Date", "Impressions", "Clicks", "Cost"]
        if goals:
            fields.append("Conversions")
        return self._report(domain, dr, "ACCOUNT_PERFORMANCE_REPORT", fields,
                            goals, attribution)

    # ---------- отчёты для истории (подённая гранулярность) ---------- #
    # kind -> (тип отчёта, поле-идентификатор, поле-подпись). Идентификатора нет
    # там, где сам ключ и есть текст (поисковая фраза, тип устройства).
    BREAKDOWNS: dict[str, tuple[str, str | None, str]] = {
        "criteria": ("CRITERIA_PERFORMANCE_REPORT", "CriterionId", "Criterion"),
        "query": ("SEARCH_QUERY_PERFORMANCE_REPORT", None, "Query"),
        "device": ("CUSTOM_REPORT", None, "Device"),
        "region": ("CUSTOM_REPORT", "TargetingLocationId", "TargetingLocationName"),
    }

    def campaigns_daily(self, domain, dr, goals=None, attribution=None) -> list[dict]:
        """Кампании с разбивкой по дням — то, что ложится в историю.

        Отдельно от ``campaigns``: там период схлопнут в одну строку на кампанию
        (дёшево для показа), здесь строка на кампанию-день (нужно для графиков и
        произвольной агрегации по неделям/месяцам).
        """
        fields = ["Date", "CampaignId", "CampaignName", "Impressions", "Clicks", "Cost"]
        if goals:
            fields.append("Conversions")
        return self._report(domain, dr, "CAMPAIGN_PERFORMANCE_REPORT", fields,
                            goals, attribution)

    def breakdown_daily(self, domain, dr, kind: str, goals=None,
                        attribution=None) -> list[dict]:
        """Подённый срез в одном из разрезов: фразы, запросы, устройства, регионы."""
        spec = self.BREAKDOWNS.get(kind)
        if spec is None:
            raise DirectError(f"Неизвестный разрез Директа: {kind}")
        report_type, id_field, text_field = spec

        fields = ["Date"]
        if kind in ("criteria", "query"):
            # только у фраз и запросов есть осмысленная привязка к кампании
            fields.append("CampaignId")
        if id_field:
            fields.append(id_field)
        fields.append(text_field)
        fields += ["Impressions", "Clicks", "Cost"]
        if goals:
            fields.append("Conversions")
        return self._report(domain, dr, report_type, fields, goals, attribution)
