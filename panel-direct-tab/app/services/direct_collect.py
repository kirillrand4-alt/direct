"""Сбор истории Директа: инкрементальное окно, бэкфилл, ночной прогон.

Повторяет логику ``app/scheduler/jobs.py`` для поисковых источников, но по
доменам, а не по ``site``.

Ключевое отличие Директа от GSC — **окно перезабора шире**. Директ правит
свежие дни несколько суток: отфильтровывает невалидные клики, пересчитывает НДС,
доначисляет корректировки. Поэтому по умолчанию каждый прогон заново тянет
последние ``REFETCH_DAYS`` дней и перезаписывает их (upsert идемпотентен).
Собирать «только вчера» — типовая ошибка, из-за которой в отчётах навсегда
остаются заниженные цифры.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.db.models_direct import DirectCollectRun
from app.providers.yandex_direct import DirectError, YandexDirectProvider
from app.services import direct_store as store

logger = logging.getLogger(__name__)

# Директ правит данные задним числом — перезабираем это окно каждый прогон.
REFETCH_DAYS = 14
# Первое окно, когда истории ещё нет вовсе.
SEED_DAYS = 90
# Бэкфилл режется на куски: длинный период Директ считает офлайн и дольше.
BACKFILL_CHUNK_DAYS = 90
# Директ хранит статистику не глубже ~3 лет; дальше запрашивать бессмысленно.
MAX_HISTORY_DAYS = 1095


class _Range:
    """Минимальный носитель периода — провайдер ждёт объект с .start/.end."""

    __slots__ = ("start", "end")

    def __init__(self, start: date, end: date):
        self.start, self.end = start, end


def settings_for(domain: str) -> tuple[list[str], str, list[str]]:
    """Настройки сбора домена: цели, модель атрибуции, список разрезов."""
    from app.credentials import get_cred

    def _cred(*keys: str) -> str:
        for k in keys:
            v = (get_cred(k) or "").strip()
            if v:
                return v
        return ""

    goals = [g.strip() for g in _cred(f"direct_goals:{domain}", "direct_goals").split(",") if g.strip()]
    attribution = _cred(f"direct_attribution:{domain}", "direct_attribution").upper()
    if attribution and attribution not in ("LC", "FC", "LSC", "LYDC", "AUTO"):
        attribution = ""
    kinds_raw = _cred(f"direct_breakdowns:{domain}", "direct_breakdowns")
    kinds = [k.strip() for k in kinds_raw.split(",") if k.strip() in YandexDirectProvider.BREAKDOWNS]
    return goals, attribution, kinds


def compute_window(db: Session, domain: str, today: date | None = None) -> _Range:
    """Какой период тянуть в очередной раз."""
    today = today or date.today()
    end = today - timedelta(days=1)  # сегодняшний день ещё неполный
    last = store.last_ok_date(db, domain)
    start = (last - timedelta(days=REFETCH_DAYS - 1)) if last else (end - timedelta(days=SEED_DAYS))
    if start > end:
        start = end
    return _Range(start, end)


def collect(db: Session, domain: str, dr, job_type: str = "daily") -> int:
    """Собрать один период по домену. Возвращает число записанных строк."""
    goals, attribution, kinds = settings_for(domain)
    # Атрибуция без целей не имеет смысла и отвергается Директом — не тащим её
    # в ключ, иначе история разъедется на пустые ветки.
    attr_key = attribution if goals else ""

    run = DirectCollectRun(domain=domain, job_type=job_type, target_date=dr.end,
                           status="pending")
    db.add(run)
    db.commit()
    run_id = run.id

    provider = YandexDirectProvider()
    attr_arg = [attribution] if (goals and attribution) else None
    try:
        total = 0
        total += store.save_daily(
            db, domain, attr_key,
            provider.daily(domain, dr, goals or None, attr_arg))
        total += store.save_campaigns(
            db, domain, attr_key,
            provider.campaigns_daily(domain, dr, goals or None, attr_arg))
        for kind in kinds:
            _, id_field, text_field = YandexDirectProvider.BREAKDOWNS[kind]
            total += store.save_breakdown(
                db, domain, kind, attr_key,
                provider.breakdown_daily(domain, dr, kind, goals or None, attr_arg),
                id_field, text_field)
        db.commit()

        run = db.get(DirectCollectRun, run_id)
        run.status, run.rows_written = "ok", total
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
        logger.info("Директ: %s строк для %s (%s..%s)", total, domain, dr.start, dr.end)
        return total
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        run = db.get(DirectCollectRun, run_id)
        if run is not None:
            run.status, run.error_text = "error", str(exc)[:2000]
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
        logger.exception("Директ: сбор для %s не удался", domain)
        raise


def backfill(db: Session, domain: str, days: int = 480) -> int:
    """Разовая закачка длинной истории — кусками, свежие первыми.

    Свежие первыми, чтобы вкладка (она смотрит на последние недели) ожила сразу,
    не дожидаясь конца загрузки.
    """
    days = max(1, min(days, MAX_HISTORY_DAYS))
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=days)
    total, chunk_end = 0, end
    while chunk_end >= start:
        chunk_start = max(chunk_end - timedelta(days=BACKFILL_CHUNK_DAYS - 1), start)
        total += collect(db, domain, _Range(chunk_start, chunk_end), job_type="backfill")
        chunk_end = chunk_start - timedelta(days=1)
    return total


def connected_domains(db: Session) -> list[str]:
    """Домены, у которых Директ подключён — только их и собираем.

    Список доменов берём тем же способом, что и страницы панели, чтобы он не
    разъезжался с селектором домена на вкладке.
    """
    from app.api.routes_pages import _domains

    provider = YandexDirectProvider()
    return [d["domain"] for d in _domains(db) if provider.is_connected(d["domain"])]


def run_daily(db: Session) -> dict[str, int]:
    """Ночной прогон по всем подключённым доменам.

    Ошибка одного домена не должна ронять остальные и уж тем более не должна
    ронять общий ночной сбор панели — поэтому каждый домен в своём try.
    """
    results: dict[str, int] = {}
    for domain in connected_domains(db):
        try:
            results[domain] = collect(db, domain, compute_window(db, domain))
        except DirectError as exc:
            logger.warning("Директ: %s пропущен — %s", domain, exc)
            results[domain] = -1
        except Exception:  # noqa: BLE001
            logger.exception("Директ: непредвиденная ошибка для %s", domain)
            results[domain] = -1
    return results
