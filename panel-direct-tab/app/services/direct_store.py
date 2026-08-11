"""Запись истории Директа в БД и чтение агрегатов из неё.

Upsert повторяет приём ``app/services/ingest.py``: диалектный
``INSERT ... ON CONFLICT DO UPDATE`` по естественному ключу. Это обязательное
свойство, а не оптимизация — Директ **пересчитывает свежие дни задним числом**
(фильтрация невалидных кликов, НДС, корректировки), поэтому одно и то же окно
собирается многократно и должно перезаписывать строки, а не плодить их.
"""
from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import date as date_type
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models_direct import (
    DirectBreakdownDaily,
    DirectCampaignDaily,
    DirectCollectRun,
    DirectDaily,
)

_CHUNK = 500


# ---------------- разбор строк отчёта ---------------- #

def _num(value, cast=float, default=0):
    """Число из ячейки TSV. Директ шлёт '--' для пустых и может слать пробелы."""
    text = str(value if value is not None else "").strip().replace("\xa0", "").replace(" ", "")
    if not text or text in ("--", "-"):
        return default
    try:
        return cast(text.replace(",", "."))
    except (TypeError, ValueError):
        return default


def _day(value) -> date_type | None:
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:40]


def _metrics(row: dict) -> dict:
    return {
        "impressions": _num(row.get("Impressions"), int),
        "clicks": _num(row.get("Clicks"), int),
        "cost": _num(row.get("Cost"), float),
        "conversions": _num(row.get("Conversions"), int),
    }


# ---------------- upsert ---------------- #

def _upsert(db: Session, model, rows: list[dict], keys: list[str]) -> int:
    if not rows:
        return 0
    dialect = db.get_bind().dialect.name
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    elif dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:  # pragma: no cover
        raise RuntimeError(f"Upsert не поддержан для диалекта {dialect!r}")

    update_cols = [c for c in rows[0] if c not in keys]
    written = 0
    for i in range(0, len(rows), _CHUNK):
        chunk = rows[i : i + _CHUNK]
        stmt = insert(model).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=keys,
            set_={c: getattr(stmt.excluded, c) for c in update_cols},
        )
        db.execute(stmt)
        written += len(chunk)
    return written


def save_daily(db: Session, domain: str, attribution: str, raw: Sequence[dict]) -> int:
    """ACCOUNT_PERFORMANCE_REPORT с полем Date → ``direct_daily``."""
    rows = []
    for r in raw:
        d = _day(r.get("Date"))
        if d is None:
            continue
        rows.append({"domain": domain, "date": d, "attribution": attribution, **_metrics(r)})
    return _upsert(db, DirectDaily, rows, ["domain", "date", "attribution"])


def save_campaigns(db: Session, domain: str, attribution: str, raw: Sequence[dict]) -> int:
    rows = []
    for r in raw:
        d = _day(r.get("Date"))
        cid = str(r.get("CampaignId") or "").strip()
        if d is None or not cid:
            continue
        rows.append({
            "domain": domain, "date": d, "campaign_id": cid,
            "campaign_name": (r.get("CampaignName") or "")[:512] or None,
            "attribution": attribution, **_metrics(r),
        })
    return _upsert(db, DirectCampaignDaily, rows,
                   ["domain", "date", "campaign_id", "attribution"])


def save_breakdown(db: Session, domain: str, kind: str, attribution: str,
                   raw: Sequence[dict], id_field: str | None, text_field: str) -> int:
    """Строки одного разреза → ``direct_breakdown_daily``.

    Ключ строки — идентификатор, если Директ его даёт (фраза, регион), иначе сам
    текст (поисковый запрос, устройство). Одинаковые ключи внутри дня
    складываются: например одна фраза в разных группах приходит несколькими
    строками, а в истории нужна одна.
    """
    merged: dict[tuple, dict] = {}
    for r in raw:
        d = _day(r.get("Date"))
        if d is None:
            continue
        text = str(r.get(text_field) or "").strip()
        key_id = str(r.get(id_field) or "").strip() if id_field else ""
        if not text and not key_id:
            continue
        key_hash = _hash(key_id or text)
        k = (d, key_hash)
        m = _metrics(r)
        if k in merged:
            cur = merged[k]
            for f in ("impressions", "clicks", "cost", "conversions"):
                cur[f] += m[f]
            continue
        merged[k] = {
            "domain": domain, "date": d, "kind": kind, "key_hash": key_hash,
            "key_id": key_id or None, "key_text": text or key_id,
            "campaign_id": str(r.get("CampaignId") or "").strip() or None,
            "attribution": attribution, **m,
        }
    return _upsert(db, DirectBreakdownDaily, list(merged.values()),
                   ["domain", "date", "kind", "key_hash", "attribution"])


# ---------------- чтение ---------------- #

_AGG = (
    func.coalesce(func.sum(DirectDaily.impressions), 0),
    func.coalesce(func.sum(DirectDaily.clicks), 0),
    func.coalesce(func.sum(DirectDaily.cost), 0.0),
    func.coalesce(func.sum(DirectDaily.conversions), 0),
)


def totals(db: Session, domain: str, start: date_type, end: date_type,
           attribution: str = "") -> dict:
    """Итоги аккаунта за период из сохранённой истории."""
    row = db.execute(
        select(*_AGG).where(
            DirectDaily.domain == domain,
            DirectDaily.attribution == attribution,
            DirectDaily.date >= start, DirectDaily.date <= end,
        )
    ).one()
    impressions, clicks, cost, conversions = row
    return {
        "impressions": int(impressions), "clicks": int(clicks),
        "cost": float(cost), "conversions": int(conversions),
        "ctr": (clicks / impressions * 100) if impressions else 0.0,
        "cpc": (cost / clicks) if clicks else 0.0,
        "cpa": (cost / conversions) if conversions else 0.0,
    }


def series(db: Session, domain: str, start: date_type, end: date_type,
           attribution: str = "") -> list[dict]:
    """Подённый ряд для графика."""
    rows = db.execute(
        select(DirectDaily.date, DirectDaily.impressions, DirectDaily.clicks,
               DirectDaily.cost, DirectDaily.conversions)
        .where(DirectDaily.domain == domain, DirectDaily.attribution == attribution,
               DirectDaily.date >= start, DirectDaily.date <= end)
        .order_by(DirectDaily.date)
    ).all()
    return [{"date": d, "impressions": i, "clicks": c, "cost": float(co),
             "conversions": cv} for d, i, c, co, cv in rows]


def campaigns(db: Session, domain: str, start: date_type, end: date_type,
              attribution: str = "") -> list[dict]:
    """Кампании за период — суммы по дням, свежее имя кампании."""
    rows = db.execute(
        select(
            DirectCampaignDaily.campaign_id,
            func.max(DirectCampaignDaily.campaign_name),
            func.sum(DirectCampaignDaily.impressions),
            func.sum(DirectCampaignDaily.clicks),
            func.sum(DirectCampaignDaily.cost),
            func.sum(DirectCampaignDaily.conversions),
        )
        .where(DirectCampaignDaily.domain == domain,
               DirectCampaignDaily.attribution == attribution,
               DirectCampaignDaily.date >= start, DirectCampaignDaily.date <= end)
        .group_by(DirectCampaignDaily.campaign_id)
    ).all()
    out = [{"campaign_id": cid, "campaign_name": name or cid,
            "impressions": int(i or 0), "clicks": int(c or 0),
            "cost": float(co or 0), "conversions": int(cv or 0)}
           for cid, name, i, c, co, cv in rows]
    out.sort(key=lambda r: r["cost"], reverse=True)
    return out


def breakdown(db: Session, domain: str, kind: str, start: date_type, end: date_type,
              attribution: str = "", limit: int = 200) -> list[dict]:
    """Топ строк одного разреза за период (по расходу)."""
    rows = db.execute(
        select(
            DirectBreakdownDaily.key_text,
            func.sum(DirectBreakdownDaily.impressions),
            func.sum(DirectBreakdownDaily.clicks),
            func.sum(DirectBreakdownDaily.cost),
            func.sum(DirectBreakdownDaily.conversions),
        )
        .where(DirectBreakdownDaily.domain == domain,
               DirectBreakdownDaily.kind == kind,
               DirectBreakdownDaily.attribution == attribution,
               DirectBreakdownDaily.date >= start, DirectBreakdownDaily.date <= end)
        .group_by(DirectBreakdownDaily.key_text)
        .order_by(func.sum(DirectBreakdownDaily.cost).desc())
        .limit(limit)
    ).all()
    return [{"key": k, "impressions": int(i or 0), "clicks": int(c or 0),
             "cost": float(co or 0), "conversions": int(cv or 0),
             "ctr": (int(c or 0) / int(i) * 100) if i else 0.0,
             "cpc": (float(co or 0) / int(c)) if c else 0.0}
            for k, i, c, co, cv in rows]


def history_bounds(db: Session, domain: str) -> tuple[date_type | None, date_type | None]:
    """Первый и последний день, за который вообще есть история."""
    row = db.execute(
        select(func.min(DirectDaily.date), func.max(DirectDaily.date))
        .where(DirectDaily.domain == domain)
    ).one()
    return row[0], row[1]


def last_run(db: Session, domain: str) -> DirectCollectRun | None:
    return db.execute(
        select(DirectCollectRun)
        .where(DirectCollectRun.domain == domain)
        .order_by(DirectCollectRun.started_at.desc())
        .limit(1)
    ).scalars().first()


def last_ok_date(db: Session, domain: str) -> date_type | None:
    """Последний успешно собранный день — точка, от которой считается окно."""
    return db.execute(
        select(func.max(DirectCollectRun.target_date))
        .where(DirectCollectRun.domain == domain, DirectCollectRun.status == "ok")
    ).scalar_one_or_none()
