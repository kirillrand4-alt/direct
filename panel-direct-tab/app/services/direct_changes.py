"""Журнал изменений настроек Директа: снапшот → сравнение → строки отличий.

Зачем вообще сравнивать. В API v5 нет сервиса, который отдавал бы историю
изменений. ``Changes.check`` возвращает только идентификаторы изменившихся
объектов и метку времени — ни поля, ни старого значения, ни автора. Читаемый
журнал есть исключительно в веб-интерфейсе Директа и наружу не отдаётся.
Поэтому единственный способ иметь историю настроек — снимать состояние самому и
запоминать отличия.

Что сторожим: настройки кампаний (статус, дневной бюджет, стратегия, временной
таргетинг, минус-фразы, запреты площадок и IP), корректировки ставок и — по
желанию — ставки по ключевым фразам.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models_direct import DirectChange, DirectSettingsSnapshot
from app.providers.yandex_direct import DirectError, YandexDirectProvider

logger = logging.getLogger(__name__)

# Ставки и корректировки Директ передаёт целым числом, умноженным на миллион —
# ровно та же ловушка, что с расходом в отчётах.
_MICRO = 1_000_000

# Человеческие подписи полей: журнал читают люди, а не разработчики.
FIELD_TITLES = {
    "Name": "название",
    "State": "состояние",
    "Status": "статус модерации",
    "StatusPayment": "оплата",
    "StartDate": "дата начала",
    "EndDate": "дата окончания",
    "DailyBudget": "дневной бюджет",
    "BiddingStrategy": "стратегия ставок",
    "TimeTargeting": "временной таргетинг",
    "NegativeKeywords": "минус-фразы",
    "BlockedIps": "запрещённые IP",
    "ExcludedSites": "запрещённые площадки",
    "Bid": "ставка на поиске",
    "ContextBid": "ставка в сетях",
    "BidModifier": "корректировка",
}

STATE_TITLES = {
    "ON": "включена", "OFF": "выключена", "SUSPENDED": "остановлена",
    "ENDED": "завершена", "ARCHIVED": "в архиве", "CONVERTED": "сконвертирована",
}


def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:40]


def _money(value) -> str:
    """Микроединицы → рубли. Директ шлёт суммы и ставки ×1 000 000."""
    try:
        return f"{int(value) / _MICRO:.2f} ₽".replace(".00 ", " ")
    except (TypeError, ValueError):
        return str(value)


def humanize(field: str, value) -> str:
    """Значение поля в виде, пригодном для чтения человеком."""
    if value is None or value == "":
        return "—"
    if field == "State":
        return STATE_TITLES.get(str(value), str(value))
    if field == "DailyBudget" and isinstance(value, dict):
        amount = _money(value.get("Amount"))
        mode = {"STANDARD": "стандартный", "DISTRIBUTED": "распределённый"}.get(
            str(value.get("Mode")), value.get("Mode") or "")
        return f"{amount} ({mode})" if mode else amount
    if field in ("Bid", "ContextBid"):
        return _money(value)
    if field == "BidModifier":
        return f"{value}%"
    if isinstance(value, list):
        # минус-фразы и списки площадок: показываем размер и первые элементы,
        # иначе одна правка забивает журнал полотном текста
        head = ", ".join(str(v) for v in value[:5])
        return f"{len(value)} шт.: {head}{' …' if len(value) > 5 else ''}" if value else "—"
    if isinstance(value, dict):
        return _canon(value)[:300]
    return str(value)


# ---------------- сбор состояния ---------------- #

def _campaign_state(row: dict) -> tuple[str, str, dict]:
    cid = str(row.get("Id") or "")
    name = str(row.get("Name") or cid)
    watched = {k: v for k, v in row.items() if k not in ("Id",)}
    return cid, name, watched


def _bid_modifier_state(row: dict) -> tuple[str, str, dict]:
    mid = str(row.get("Id") or "")
    kind = str(row.get("Type") or "корректировка")
    scope = f"кампания {row.get('CampaignId')}" if row.get("CampaignId") else \
            f"группа {row.get('AdGroupId')}"
    # значение корректировки лежит внутри вложенного объекта своего типа
    value = None
    for key, sub in row.items():
        if isinstance(sub, dict) and "BidModifier" in sub:
            value = sub["BidModifier"]
            break
    return mid, f"{kind} · {scope}", {"BidModifier": value}


def _keyword_bid_state(row: dict) -> tuple[str, str, dict]:
    kid = str(row.get("KeywordId") or "")
    return kid, f"фраза {kid}", {"Bid": row.get("Bid"), "ContextBid": row.get("ContextBid")}


COLLECTORS = {
    "campaign": ("get_campaigns", _campaign_state),
    "bid_modifier": ("get_bid_modifiers", _bid_modifier_state),
    "keyword_bid": ("get_keyword_bids", _keyword_bid_state),
}


# ---------------- сравнение ---------------- #

def _diff(old: dict, new: dict) -> list[tuple[str, object, object]]:
    """Отличия двух состояний: [(поле, было, стало)]."""
    out = []
    for key in sorted(set(old) | set(new)):
        a, b = old.get(key), new.get(key)
        if _canon(a) != _canon(b):
            out.append((key, a, b))
    return out


def sync_object_type(db: Session, domain: str, object_type: str,
                     rows: list[dict]) -> list[DirectChange]:
    """Сверяет свежее состояние со снапшотом и пишет отличия в журнал.

    Первый прогон только запоминает состояние и **не пишет изменений**: иначе
    журнал открылся бы сотней строк «появилось» про всё, что и так было.
    """
    _, state_of = COLLECTORS[object_type]
    now = datetime.now(timezone.utc)

    stored = {
        s.object_id: s for s in db.execute(
            select(DirectSettingsSnapshot).where(
                DirectSettingsSnapshot.domain == domain,
                DirectSettingsSnapshot.object_type == object_type,
            )
        ).scalars()
    }
    first_run = not stored
    changes: list[DirectChange] = []
    seen: set[str] = set()

    for row in rows:
        oid, name, state = state_of(row)
        if not oid:
            continue
        seen.add(oid)
        payload = _canon(state)
        digest = _hash(payload)
        prev = stored.get(oid)

        if prev is None:
            if not first_run:
                changes.append(DirectChange(
                    domain=domain, detected_at=now, object_type=object_type,
                    object_id=oid, object_name=name, kind="added",
                    field="—", old_value=None, new_value="создано"))
            db.add(DirectSettingsSnapshot(
                domain=domain, object_type=object_type, object_id=oid,
                object_name=name, payload=payload, payload_hash=digest, updated_at=now))
            continue

        if prev.payload_hash == digest:
            continue  # ничего не поменялось — дешёвая проверка по хешу

        try:
            before = json.loads(prev.payload)
        except ValueError:
            before = {}
        for field, was, now_val in _diff(before, state):
            changes.append(DirectChange(
                domain=domain, detected_at=now, object_type=object_type,
                object_id=oid, object_name=name, kind="changed", field=field,
                old_value=humanize(field, was), new_value=humanize(field, now_val)))
        prev.payload, prev.payload_hash = payload, digest
        prev.object_name, prev.updated_at = name, now

    # объект пропал из выдачи — удалён или потерял видимость
    if not first_run:
        for oid, prev in stored.items():
            if oid in seen:
                continue
            changes.append(DirectChange(
                domain=domain, detected_at=now, object_type=object_type,
                object_id=oid, object_name=prev.object_name, kind="removed",
                field="—", old_value="было", new_value="исчезло из выдачи"))
            db.delete(prev)

    for ch in changes:
        db.add(ch)
    return changes


def sync(db: Session, domain: str, object_types: list[str] | None = None) -> int:
    """Полная сверка настроек домена. Возвращает число записанных изменений.

    Каждый тип объектов — в своём ``try``: незнакомое имя поля в одном сервисе
    не должно лишать журнала по остальным.
    """
    provider = YandexDirectProvider()
    types = object_types or ["campaign", "bid_modifier"]
    total = 0
    # Список кампаний нужен и снапшоту кампаний, и корректировкам (те без
    # CampaignIds не читаются вовсе). Читаем его один раз за сверку.
    campaign_ids: list | None = None
    for object_type in types:
        method_name, _ = COLLECTORS[object_type]
        try:
            if object_type == "bid_modifier":
                rows = provider.get_bid_modifiers(domain, campaign_ids=campaign_ids)
            else:
                rows = getattr(provider, method_name)(domain)
        except DirectError as exc:
            logger.warning("Директ: настройки «%s» для %s не прочитаны — %s",
                           object_type, domain, exc)
            continue
        if object_type == "campaign":
            campaign_ids = [r.get("Id") for r in rows if r.get("Id") is not None]
        total += len(sync_object_type(db, domain, object_type, rows))
    return total


def recent(db: Session, domain: str, limit: int = 100) -> list[DirectChange]:
    """Последние изменения для показа на вкладке."""
    return list(db.execute(
        select(DirectChange).where(DirectChange.domain == domain)
        .order_by(DirectChange.detected_at.desc(), DirectChange.id.desc())
        .limit(limit)
    ).scalars())
