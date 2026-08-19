"""Подбор целей Метрики для домена: какие серии конверсий заводить в Директе.

Наборы целей можно вписать руками, но их надо откуда-то взять: у холдинга это
663 цели на 22 счётчика, и нужные среди них — шесть штук, созданных интеграцией
Roistat. Модуль находит их сам и предлагает готовый текст для формы.

**Почему по идентификатору условия, а не по названию.** Цели Roistat — типа
``action``, и в условии лежит машинный идентификатор: ``lead_status``,
``qualified_lead``, ``deal_success``, ``spam_lead``. Название же человек правит
руками, и оно расходится: на одном счётчике «[Roistat] Сделка успешна», на
другом «Roistat] Сделка успешна» со сломанной скобкой, на третьем просто
«Roistat Сделка». Поиск по имени такие цели теряет, поиск по идентификатору —
нет.

**Как находится счётчик домена.** По самим визитам: берётся тот счётчик, с
которого пришло больше всего визитов на этот домен. Реестра «домен → счётчик» в
панели нет, а визиты его уже содержат — так же поступает ``metrika_resync``.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.request
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_API = "https://api-metrika.yandex.net/management/v1"

# Идентификатор цели Roistat -> имя серии в истории. Порядок задаёт и порядок
# серий: первая собирает разрезы, поэтому впереди то, что важнее для решений.
ROISTAT_SERIES: list[tuple[str, str]] = [
    ("qualified_lead", "Квал"),
    ("lead_status", "Лид 1"),
    ("spam_lead", "Спам"),
    ("deal_success", "Сделка"),
]

# Сколько последних визитов смотреть, определяя счётчик домена.
_COUNTER_LOOKBACK = 20000


def _token() -> str | None:
    from app.config import get_settings
    from app.credentials import get_cred

    return (get_cred("yandex_metrika_token") or get_cred("yandex_wm_token")
            or get_settings().yandex_metrika_oauth_token)


def _host(url: str | None) -> str | None:
    m = re.match(r"https?://([^/?#]+)", url or "")
    if not m:
        return None
    host = m.group(1).lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def counter_for(db: Session, domain: str) -> int | None:
    """Счётчик Метрики домена — по большинству визитов.

    Домен берём из ``start_url``, а не из ``site_id``: визиты Метрики лежат на
    произвольных площадках панели, и достоверен только адрес входа.
    """
    from app.db.models import Visit

    rows = db.execute(
        select(Visit.counter_id, Visit.start_url)
        .where(Visit.counter_id.is_not(None))
        .order_by(Visit.id.desc())
        .limit(_COUNTER_LOOKBACK)
    ).all()
    hits: Counter = Counter()
    for counter, start_url in rows:
        if _host(start_url) == domain:
            hits[int(counter)] += 1
    return hits.most_common(1)[0][0] if hits else None


def goals_of(counter: int) -> dict[str, tuple[str, str]]:
    """Цели счётчика: идентификатор условия -> (id цели, название).

    Берётся первая цель с таким идентификатором: дубли встречаются (цель
    пересоздавали), и лишние только зашумили бы предложение.
    """
    token = _token()
    if not token:
        logger.warning("Метрика: токен не задан — цели подобрать нечем")
        return {}
    try:
        request = urllib.request.Request(
            f"{_API}/counter/{counter}/goals",
            headers={"Authorization": f"OAuth {token}"})
        with urllib.request.urlopen(request, timeout=60) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 — подбор целей не критичен для остального
        logger.exception("Метрика: цели счётчика %s не прочитались", counter)
        return {}

    out: dict[str, tuple[str, str]] = {}
    for goal in payload.get("goals") or []:
        goal_id, name = str(goal.get("id")), (goal.get("name") or "")
        for condition in goal.get("conditions") or []:
            ident = str(condition.get("url") or "").strip().lower()
            if ident and ident not in out:
                out[ident] = (goal_id, name)
    return out


def suggest(db: Session, domain: str) -> tuple[str, list[str]]:
    """Готовый текст наборов целей для домена и человекочитаемый отчёт.

    Возвращает ``("Квал=123\\nЛид 1=456", ["Квал -> [Roistat] Квалифицированный лид", …])``.
    Пустой текст означает, что целей Roistat на счётчике нет — вписывать нечего.
    """
    counter = counter_for(db, domain)
    if counter is None:
        return "", [f"{domain}: счётчик Метрики не найден среди визитов"]

    goals = goals_of(counter)
    if not goals:
        return "", [f"{domain}: счётчик {counter}, целей не прочитано"]

    lines, report = [], [f"{domain}: счётчик {counter}"]
    for ident, series in ROISTAT_SERIES:
        found = goals.get(ident)
        if not found:
            report.append(f"   {series}: цели «{ident}» на счётчике нет")
            continue
        goal_id, name = found
        lines.append(f"{series}={goal_id}")
        report.append(f"   {series} → {name} (id {goal_id})")
    return "\n".join(lines), report


def apply_suggested(db: Session, domains, overwrite: bool = False) -> list[tuple[str, str]]:
    """Проставить подобранные наборы. Возвращает пары (домен, набор).

    ``overwrite=False`` не трогает домены, где наборы уже заданы: подбор — это
    подсказка, а осознанно выбранные цели важнее её.
    """
    from app.credentials import get_cred, set_cred

    done: list[tuple[str, str]] = []
    for domain in domains:
        if not overwrite and (get_cred(f"direct_goal_sets:{domain}") or "").strip():
            continue
        text, _report = suggest(db, domain)
        if not text:
            continue
        set_cred(f"direct_goal_sets:{domain}", text)
        done.append((domain, text.replace("\n", " | ")))
    return done
