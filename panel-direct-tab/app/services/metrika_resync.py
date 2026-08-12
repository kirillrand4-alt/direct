"""Еженедельный пересинк визитов Метрики: хвост в 30 дней перекачивается заново.

Зачем. Цели Roistat (квалификация лида, коллтрекинг, емейлтрекинг) досылаются в
Метрику **после** визита — в момент смены статуса в CRM, до трёх недель спустя.
Панель же скачивает визиты через Logs API по свежим дням, и досланные позже цели
в уже скачанные строки не попадают. Наглядно: июль в БД показывал 42 квала при
53 в интерфейсе Метрики, июнь — 31 при 53. Перекачка хвоста закрывает разрыв:
``import_tsv(update=True)`` перезаписывает строки по ``visit_id``.

Раз в неделю, а не каждую ночь: полная перекачка месяца — это сотни тысяч строк
через Logs API, каждую ночь она избыточна (Метрика принимает офлайн-конверсии
максимум 21 день назад, недельный шаг с 30-дневным окном покрывает это с запасом).
"""
from __future__ import annotations

import io
import logging
import time
from datetime import date, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_API = "https://api-metrika.yandex.net"
RESYNC_DAYS = 30
# Опрос готовности заявки Logs API: интервал и потолок ожидания.
_POLL_SEC = 30
_MAX_WAIT_SEC = 25 * 60


def _token() -> str | None:
    from app.config import get_settings
    from app.credentials import get_cred

    return (get_cred("yandex_metrika_token") or get_cred("yandex_wm_token")
            or get_settings().yandex_metrika_oauth_token)


def _counters_by_site(db: Session) -> dict[int, int]:
    """site_id -> counter_id по уже накопленным визитам.

    Счётчик берём из самих данных, а не из настроек: у панели нет реестра
    «сайт → счётчик Метрики», а визиты его уже содержат.
    """
    from sqlalchemy import func
    from app.db.models import Visit

    rows = db.execute(
        select(Visit.site_id, Visit.counter_id, func.count())
        .where(Visit.counter_id.is_not(None))
        .group_by(Visit.site_id, Visit.counter_id)
    ).all()
    best: dict[int, tuple[int, int]] = {}
    for sid, counter, n in rows:
        if counter and (sid not in best or n > best[sid][1]):
            best[sid] = (int(counter), n)
    return {sid: c for sid, (c, _n) in best.items()}


def resync_counter(db: Session, site_id: int, counter: int, token: str,
                   days: int = RESYNC_DAYS) -> int:
    """Перекачивает визиты одного счётчика за последние ``days`` дней."""
    from app.services.visits import VISIT_FIELDS, import_tsv

    d2 = date.today() - timedelta(days=1)   # сегодняшний день неполный
    d1 = d2 - timedelta(days=days - 1)
    headers = {"Authorization": f"OAuth {token}"}

    resp = httpx.post(
        f"{_API}/management/v1/counter/{counter}/logrequests",
        params={"date1": d1.isoformat(), "date2": d2.isoformat(),
                "source": "visits", "fields": ",".join(VISIT_FIELDS)},
        headers=headers, timeout=60)
    lr = (resp.json() or {}).get("log_request") or {}
    rid = lr.get("request_id")
    if not rid:
        raise RuntimeError(f"Logs API не принял заявку: {resp.text[:300]}")

    deadline = time.time() + _MAX_WAIT_SEC
    status, parts = lr.get("status"), []
    while status in ("created", "processing") and time.time() < deadline:
        time.sleep(_POLL_SEC)
        j = httpx.get(f"{_API}/management/v1/counter/{counter}/logrequest/{rid}",
                      headers=headers, timeout=30).json()
        lr = j.get("log_request") or {}
        status, parts = lr.get("status"), lr.get("parts") or []
    if status != "processed":
        # заявку не бросаем висеть — чистим, придём через неделю
        httpx.post(f"{_API}/management/v1/counter/{counter}/logrequest/{rid}/clean",
                   headers=headers, timeout=30)
        raise RuntimeError(f"заявка {rid} не готова за отведённое время (статус {status})")

    total = 0
    for p in parts:
        n = p.get("part_number")
        text = httpx.get(
            f"{_API}/management/v1/counter/{counter}/logrequest/{rid}/part/{n}/download",
            headers=headers, timeout=300).text
        lines: io.StringIO
        first = text.split("\n", 1)[0]
        if "visitID" not in first:  # часть без заголовка — приклеиваем свой
            lines = io.StringIO("\t".join(VISIT_FIELDS) + "\n" + text)
        else:
            lines = io.StringIO(text)
        total += import_tsv(db, site_id, lines, update=True)

    httpx.post(f"{_API}/management/v1/counter/{counter}/logrequest/{rid}/clean",
               headers=headers, timeout=30)
    logger.info("Метрика: пересинк счётчика %s (site %s) — %s строк за %s..%s",
                counter, site_id, total, d1, d2)
    return total


def run_weekly(db: Session) -> dict[int, int]:
    """Пересинк всех счётчиков. Ошибка одного не роняет остальные."""
    token = _token()
    if not token:
        logger.warning("Метрика: пересинк пропущен — нет токена")
        return {}
    results: dict[int, int] = {}
    for site_id, counter in _counters_by_site(db).items():
        try:
            results[site_id] = resync_counter(db, site_id, counter, token)
        except Exception:  # noqa: BLE001
            logger.exception("Метрика: пересинк site %s не удался", site_id)
            results[site_id] = -1
    return results
