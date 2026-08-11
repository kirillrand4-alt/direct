"""Вкладка «Директ»: рекламная статистика (живой запрос к Reports API, по домену).

Отдельный роутер (не правит большой ``routes_pages.py``) — так изменение
добавочное и безопаснее ложится при деплое. Настройки токена/логина живут прямо
на вкладке, поэтому ``admin.html`` трогать не нужно.
"""
from __future__ import annotations

import csv
import io
import logging
import threading
from datetime import timedelta
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.deps import get_db, parse_date_range
from app.web import templates

# Импорт ради побочного эффекта: модели должны попасть в Base.metadata до того,
# как lifespan вызовет init_db(). Роутеры импортируются на старте main.py, то
# есть раньше — поэтому create_all создаст таблицы Директа вместе с остальными.
from app.db import models_direct  # noqa: F401

router = APIRouter(tags=["direct"], include_in_schema=False)
BP = get_settings().base_path  # "" или, напр., "/stat" — для целей редиректов


def _domain_list(db: Session) -> list[dict]:
    """Список доменов панели. Используем тот же вывод, что и страницы (по сайтам)."""
    from app.api.routes_pages import _domains

    return _domains(db)


def _resolve_domain(domains: list[dict], domain: str | None) -> str | None:
    if domain and any(d["domain"] == domain for d in domains):
        return domain
    return domains[0]["domain"] if domains else None


@router.get("/direct")
def direct_page(request: Request, domain: str | None = None,
                start: str | None = None, end: str | None = None,
                kind: str | None = None,
                msg: str | None = None, db: Session = Depends(get_db)):
    from app.credentials import get_cred
    from app.services.direct import direct_overview

    domains = _domain_list(db)
    cur = _resolve_domain(domains, domain)
    dr = parse_date_range(start, end)
    data = direct_overview(cur, dr) if cur else {
        "connected": False, "error": None, "totals": None,
        "daily": [], "campaigns": [], "has_conversions": False,
    }
    ctx = {
        "request": request,
        "msg": msg,
        "domains": domains,
        "cur_domain": cur,
        "range": dr,
        "token_set": bool((get_cred("yandex_direct_token") or "").strip()),
        "login": get_cred(f"direct_login:{cur}") if cur else None,
        **data,
        **_history_ctx(db, cur, dr, kind),
    }
    return templates.TemplateResponse(request, "direct.html", ctx)


def _history_ctx(db: Session, domain: str | None, dr, kind: str | None) -> dict:
    """Всё, что читается из сохранённой истории, а не из живого запроса.

    История может отсутствовать (сбор ещё не отработал) — тогда блок на вкладке
    просто не показывается, а живой просмотр продолжает работать как раньше.
    """
    from app.providers.yandex_direct import YandexDirectProvider
    from app.services import direct_collect, direct_store as store

    kinds = list(YandexDirectProvider.BREAKDOWNS)
    empty = {"hist": None, "kind": kind if kind in kinds else None, "kinds": kinds,
             "goals": "", "attribution": "", "breakdowns": "", "last_run": None,
             "changes": [], "field_titles": {}, "track_bids": False}
    if not domain:
        return empty

    goals, attribution, enabled_kinds = direct_collect.settings_for(domain)
    attr_key = attribution if goals else ""
    from app.credentials import get_cred
    from app.services import direct_changes
    empty.update({"goals": ",".join(goals), "attribution": attribution,
                  "breakdowns": ",".join(enabled_kinds),
                  "last_run": store.last_run(db, domain),
                  # журнал изменений не зависит от наличия статистики
                  "changes": direct_changes.recent(db, domain, limit=60),
                  "field_titles": direct_changes.FIELD_TITLES,
                  "track_bids": bool((get_cred(f"direct_track_bids:{domain}") or "").strip())})

    first, last = store.history_bounds(db, domain)
    if first is None:
        return empty

    span = (dr.end - dr.start).days + 1
    prev_end = dr.start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=span - 1)
    cur_kind = kind if kind in kinds else (enabled_kinds[0] if enabled_kinds else None)

    empty["kind"] = cur_kind
    empty["hist"] = {
        "first": first, "last": last,
        "totals": store.totals(db, domain, dr.start, dr.end, attr_key),
        "prev": store.totals(db, domain, prev_start, prev_end, attr_key),
        "prev_range": (prev_start, prev_end),
        "series": store.series(db, domain, dr.start, dr.end, attr_key),
        "campaigns": store.campaigns(db, domain, dr.start, dr.end, attr_key),
        "breakdown": (store.breakdown(db, domain, cur_kind, dr.start, dr.end, attr_key)
                      if cur_kind else []),
        "enabled_kinds": enabled_kinds,
    }
    return empty


@router.get("/direct/export")
def direct_export(domain: str | None = None, start: str | None = None,
                  end: str | None = None, db: Session = Depends(get_db)):
    from app.services.direct import direct_overview

    domains = _domain_list(db)
    cur = _resolve_domain(domains, domain)
    dr = parse_date_range(start, end)
    data = direct_overview(cur, dr) if cur else {"campaigns": [], "has_conversions": False}

    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    head = ["Кампания", "Показы", "Клики", "CTR %", "Расход, ₽", "CPC, ₽"]
    if data.get("has_conversions"):
        head += ["Конверсии", "CPA, ₽"]
    w.writerow(head)
    for c in data.get("campaigns", []):
        row = [c["name"], c["impressions"], c["clicks"], f"{c['ctr'] * 100:.2f}",
               f"{c['cost']:.2f}", f"{c['cpc']:.2f}"]
        if data.get("has_conversions"):
            cpa = (c["cost"] / c["conversions"]) if c["conversions"] else 0
            row += [c["conversions"], f"{cpa:.2f}"]
        w.writerow(row)

    payload = ("﻿" + buf.getvalue()).encode("utf-8")  # BOM → Excel видит кириллицу
    fname = f"direct-{cur or 'all'}-{dr.start}-{dr.end}.csv"
    return StreamingResponse(
        io.BytesIO(payload),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.post("/ui/direct/token")
def ui_direct_token(token: str = Form("")):
    from app.credentials import set_cred

    token = (token or "").strip()
    set_cred("yandex_direct_token", token)
    msg = "Токен Директа сохранён." if token else "Токен Директа очищен."
    # nocache=1 → страница пересчитается сразу, минуя серверный HTML-кэш
    return RedirectResponse(url=f"{BP}/direct?nocache=1&msg={quote(msg)}", status_code=303)


@router.post("/ui/direct/login")
def ui_direct_login(domain: str = Form(...), login: str = Form(""), token: str = Form("")):
    from app.credentials import set_cred

    domain = (domain or "").strip()
    login = (login or "").strip()
    token = (token or "").strip()
    if not domain:
        return RedirectResponse(url=f"{BP}/direct?msg={quote('Не указан домен.')}", status_code=303)
    set_cred(f"direct_login:{domain}", login)
    if token:
        set_cred(f"direct_token:{domain}", token)
    msg = f"Директ: настройки для {domain} сохранены."
    return RedirectResponse(
        url=f"{BP}/direct?domain={quote(domain)}&nocache=1&msg={quote(msg)}", status_code=303
    )


@router.post("/ui/direct/collect-settings")
def ui_direct_collect_settings(domain: str = Form(...), goals: str = Form(""),
                               attribution: str = Form(""),
                               breakdowns: list[str] = Form(default=[]),
                               track_bids: str = Form("")):
    """Что именно собирать по домену: цели, модель атрибуции, набор разрезов."""
    from app.credentials import set_cred
    from app.providers.yandex_direct import YandexDirectProvider

    domain = (domain or "").strip()
    if not domain:
        return RedirectResponse(url=f"{BP}/direct?msg={quote('Не указан домен.')}",
                                status_code=303)

    goals_clean = ",".join(g.strip() for g in goals.split(",") if g.strip().isdigit())
    attr = (attribution or "").strip().upper()
    if attr not in ("LC", "FC", "LSC", "LYDC", "AUTO"):
        attr = ""
    kinds = ",".join(k for k in breakdowns if k in YandexDirectProvider.BREAKDOWNS)

    set_cred(f"direct_goals:{domain}", goals_clean)
    set_cred(f"direct_attribution:{domain}", attr)
    set_cred(f"direct_breakdowns:{domain}", kinds)
    set_cred(f"direct_track_bids:{domain}", "1" if track_bids else "")

    msg = ("Настройки сбора сохранены. Новые разрезы и атрибуция появятся после "
           "ближайшего сбора — можно запустить его кнопкой.")
    return RedirectResponse(
        url=f"{BP}/direct?domain={quote(domain)}&nocache=1&msg={quote(msg)}", status_code=303)


def _collect_in_thread(domain: str, days: int | None) -> None:
    """Сбор в фоне: длинная история занимает минуты, держать HTTP-запрос нельзя.

    Своя сессия БД — сессия запроса закроется сразу после редиректа.
    """
    def _run():
        from app.db.base import SessionLocal
        from app.services import direct_collect

        db = SessionLocal()
        try:
            if days:
                direct_collect.backfill(db, domain, days)
            else:
                direct_collect.collect(db, domain,
                                       direct_collect.compute_window(db, domain),
                                       job_type="manual")
        except Exception:  # noqa: BLE001 — уже записано в журнал сборов
            logging.getLogger(__name__).exception("Директ: ручной сбор для %s упал", domain)
        finally:
            db.close()

    threading.Thread(target=_run, daemon=True).start()


@router.post("/ui/direct/collect")
def ui_direct_collect(domain: str = Form(...), days: int = Form(0)):
    """Собрать сейчас: обычное окно (days=0) или историю за N дней."""
    domain = (domain or "").strip()
    if not domain:
        return RedirectResponse(url=f"{BP}/direct?msg={quote('Не указан домен.')}",
                                status_code=303)
    _collect_in_thread(domain, days if days > 0 else None)
    msg = (f"Запущена загрузка истории за {days} дн. — идёт в фоне, обновите страницу через минуту."
           if days > 0 else "Сбор запущен — идёт в фоне, обновите страницу через минуту.")
    return RedirectResponse(
        url=f"{BP}/direct?domain={quote(domain)}&nocache=1&msg={quote(msg)}", status_code=303)
