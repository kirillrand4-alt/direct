"""Вкладка «Директ»: рекламная статистика (живой запрос к Reports API, по домену).

Отдельный роутер (не правит большой ``routes_pages.py``) — так изменение
добавочное и безопаснее ложится при деплое. Настройки токена/логина живут прямо
на вкладке, поэтому ``admin.html`` трогать не нужно.
"""
from __future__ import annotations

import csv
import io
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.deps import get_db, parse_date_range
from app.web import templates

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
    }
    return templates.TemplateResponse(request, "direct.html", ctx)


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
