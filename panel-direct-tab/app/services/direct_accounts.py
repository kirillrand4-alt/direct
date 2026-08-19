"""Разведка рекламных кабинетов Директа и массовая привязка их к доменам.

**Зачем это отдельно от настроек вкладки.** Провайдер давно умеет ходить в чужой
кабинет: ``Client-Login`` берётся из кредов по домену (``direct_login:<домен>``),
и ночной сбор обходит все домены, у которых привязка есть. Не хватало ровно
одного — способа эту привязку получить. В форме вкладки логин вводится по одному
домену за раз, а у холдинга их два десятка: 21 переключение домена и 21
сохранение, причём логин надо откуда-то знать заранее.

**Почему логин нельзя вывести из домена.** Яндекс выдаёт кабинетам логины вида
``brand-a-100001-k7x2``, ``auto-a1b2c3d4``, ``main-100003-r4v7`` — домена в
них нет. Перебор доменных вариантов (``brand-a``, ``branda-site``…)
даёт ошибку 8800 «объект не найден» и ничего не находит.

**Почему нельзя спросить список у API.** ``AgencyClients.get`` отвечает ошибкой
54 «оператор не является представителем агентства»: у обычного (неагентского)
аккаунта перечислить доступные кабинеты нечем. Логины видны только человеку — в
интерфейсе Директ Про, в списке «Доступные аккаунты».

Отсюда порядок: человек вставляет список логинов из интерфейса, разведка по
каждому спрашивает у API, чей это кабинет и какие в нём кампании, определяет
домен **по ссылкам объявлений** — и только после показа результата привязки
пишутся в креды.
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.db.models_direct import DirectAccount, ensure_direct_schema
from app.providers.yandex_direct import DirectError, YandexDirectProvider

logger = logging.getLogger(__name__)

# Схему досматриваем один раз за процесс: обновление вкладки могло добавить
# колонки в таблицу, созданную прошлой версией (см. ensure_direct_schema).
_schema_checked = False


def _ensure_schema() -> None:
    global _schema_checked
    if _schema_checked:
        return
    from app.db.base import engine

    ensure_direct_schema(engine)
    _schema_checked = True

# За какой период смотреть расход кабинета. Достаточно длинно, чтобы сезонная
# пауза в одном бренде не выглядела как заброшенный кабинет.
SPEND_WINDOW_DAYS = 90

# Сколько ссылок объявлений достаточно, чтобы считать домен опознанным. Больше
# не запрашиваем: у крупного кабинета объявлений десятки тысяч, а домен у них
# один и тот же.
ENOUGH_HREFS = 60
# Потолок запросов объявлений на один кабинет — страховка от аккаунта, где
# текстовых объявлений нет вовсе (только динамические и смарт-баннеры) и ссылки
# не наберутся никогда.
MAX_AD_REQUESTS = 12

# Живые состояния: кампания либо крутится, либо остановлена вручную и может быть
# включена обратно. Остальные (ENDED/CONVERTED/ARCHIVED) — прошлое кабинета.
LIVE_STATES = ("ON", "SUSPENDED")

_HOST_RE = re.compile(r"^https?://([^/?#]+)", re.I)
# Логин Яндекса: латиница, цифры, дефис, точка, подчёркивание. Кириллицы в нём
# не бывает — на этом и держится отсев подписей вида «Бренд A · Редактор».
_LOGIN_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,62}$", re.I)


def host_of(href: str) -> str | None:
    """Хост из ссылки объявления, без ``www.`` и порта."""
    m = _HOST_RE.match((href or "").strip())
    if not m:
        return None
    host = m.group(1).lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def parse_logins(text: str) -> list[str]:
    """Логины из текста, вставленного из интерфейса Директ Про.

    Список там идёт парами строк: сам логин, под ним подпись «Бренд A · Редактор».
    Подпись отбрасываем по разделителю «·» и по кириллице; всё остальное, что
    похоже на логин, оставляем в исходном порядке без повторов.
    """
    out: list[str] = []
    seen: set[str] = set()
    for raw_line in (text or "").replace(",", "\n").replace(";", "\n").splitlines():
        line = raw_line.strip()
        if not line or "·" in line or re.search(r"[а-яё]", line, re.I):
            continue
        for token in line.split():
            token = token.strip().strip(".")
            if not _LOGIN_RE.match(token):
                continue
            key = token.lower()
            if key not in seen:
                seen.add(key)
                out.append(token)
    return out


def _sample_hrefs(provider: YandexDirectProvider, login: str,
                  campaigns: list[dict]) -> Counter:
    """Счётчик хостов по ссылкам объявлений кабинета.

    Живые кампании идут первыми: домен берём из того, что откручивается сейчас,
    а не из архива пятилетней давности, где сайт мог быть другим.
    """
    ordered = ([c for c in campaigns if c.get("State") in LIVE_STATES]
               + [c for c in campaigns if c.get("State") not in LIVE_STATES])
    ids = [c.get("Id") for c in ordered if c.get("Id") is not None]

    hosts: Counter = Counter()
    requests = 0
    chunk = provider.ADS_CAMPAIGN_CHUNK
    for i in range(0, len(ids), chunk):
        if sum(hosts.values()) >= ENOUGH_HREFS or requests >= MAX_AD_REQUESTS:
            break
        requests += 1
        try:
            hrefs = provider.ad_hrefs(ids[i:i + chunk], login=login)
        except DirectError as exc:
            # Объявления — уточнение, а не суть: кабинет уже опознан по clients.get.
            logger.warning("Директ: объявления кабинета %s не прочитались — %s", login, exc)
            break
        for href in hrefs:
            host = host_of(href)
            if host:
                hosts[host] += 1
    return hosts


def pick_domain(hosts: Counter, panel_domains: set[str]) -> str | None:
    """Домен кабинета: самый частый хост, который панель отслеживает.

    Приоритет домену панели, а не абсолютному большинству: у кампании с квизом
    все ссылки ведут на конструктор форм (``quiz-service.example``), и по чистому большинству
    кабинет опознался бы как квиз-сервис. Если ни один хост панели не встретился,
    отдаём самый частый как есть — чтобы на вкладке было видно, что кабинет
    рекламирует сайт, которого в панели нет.
    """
    for host, _ in hosts.most_common():
        if host in panel_domains:
            return host
    top = hosts.most_common(1)
    return top[0][0] if top else None


class _Window:
    """Носитель периода для отчёта — провайдер ждёт объект с .start/.end."""

    __slots__ = ("start", "end")

    def __init__(self, start: date, end: date):
        self.start, self.end = start, end


def spend_window(today: date | None = None) -> _Window:
    end = (today or date.today()) - timedelta(days=1)  # сегодня ещё неполный
    return _Window(end - timedelta(days=SPEND_WINDOW_DAYS - 1), end)


def probe(provider: YandexDirectProvider, login: str,
          panel_domains: set[str]) -> dict:
    """Один кабинет: чей он, сколько кампаний, на какой домен рекламирует."""
    row = {"login": login, "client_id": None, "client_info": None,
           "campaigns": 0, "live_campaigns": 0, "clicks_90d": 0, "cost_90d": 0.0,
           "domain": None, "hosts": Counter(), "status": "ok", "error_text": None}
    try:
        info = provider.get_client_info(login=login)
    except DirectError as exc:
        row["status"], row["error_text"] = "error", str(exc)
        return row

    row["client_id"] = str(info.get("ClientId") or "") or None
    row["client_info"] = (info.get("ClientInfo") or None)

    try:
        campaigns = provider.list_campaigns_brief(login=login)
    except DirectError as exc:
        row["status"], row["error_text"] = "error", str(exc)
        return row

    row["campaigns"] = len(campaigns)
    row["live_campaigns"] = sum(1 for c in campaigns if c.get("State") in LIVE_STATES)

    if campaigns:
        # Расход — уточнение, а не суть: кабинет уже опознан. Отчёт заказывается
        # офлайн и может не успеть, поэтому его отсутствие не делает строку
        # ошибочной, просто оставляет нули.
        try:
            totals = provider.account_totals(spend_window(), login=login)
            row["clicks_90d"] = int(totals.get("clicks") or 0)
            row["cost_90d"] = float(totals.get("cost") or 0.0)
        except DirectError as exc:
            logger.warning("Директ: расход кабинета %s не прочитался — %s", login, exc)

    hosts = _sample_hrefs(provider, login, campaigns)
    row["hosts"] = hosts
    row["domain"] = pick_domain(hosts, panel_domains)
    return row


def scan(db: Session, logins: list[str], panel_domains: set[str],
         provider: YandexDirectProvider | None = None) -> list[DirectAccount]:
    """Опросить логины и сохранить результат. Возвращает строки в порядке ввода.

    Кабинет, который перестал открываться, не удаляем, а помечаем ошибкой: иначе
    после разового сбоя сети исчезла бы и привязка, и понимание, что она была.
    """
    _ensure_schema()
    provider = provider or YandexDirectProvider()
    saved: list[DirectAccount] = []
    for login in logins:
        data = probe(provider, login, panel_domains)
        acc = db.query(DirectAccount).filter(DirectAccount.login == login).one_or_none()
        if acc is None:
            acc = DirectAccount(login=login)
            db.add(acc)
        acc.client_id = data["client_id"]
        acc.client_info = data["client_info"]
        acc.campaigns = data["campaigns"]
        acc.live_campaigns = data["live_campaigns"]
        acc.clicks_90d = data["clicks_90d"]
        acc.cost_90d = data["cost_90d"]
        acc.domain = data["domain"]
        acc.domains = json.dumps(dict(data["hosts"].most_common(12)), ensure_ascii=False)
        acc.status = data["status"]
        acc.error_text = (data["error_text"] or None)
        acc.scanned_at = datetime.now(timezone.utc)
        saved.append(acc)
    db.commit()
    logger.info("Директ: разведка кабинетов — опрошено %d, с доступом %d",
                len(saved), sum(1 for a in saved if a.status == "ok"))
    return saved


def _rank(acc: DirectAccount) -> tuple:
    """Насколько кабинет «настоящий»: сначала деньги, потом кампании.

    Расход первым не случайно. На brand-c.example заведены два кабинета, в
    каждом по одной живой кампании — по кампаниям они неотличимы, и привязка
    доставалась тому, чей логин раньше по алфавиту. А деньги идут только через
    один из них: у второго за 90 дней ноль показов. По расходу выбор
    однозначен.
    """
    return (acc.cost_90d or 0.0, acc.clicks_90d or 0,
            acc.live_campaigns or 0, acc.campaigns or 0)


def listing(db: Session) -> list[DirectAccount]:
    """Найденные кабинеты: сначала те, через которые идут деньги."""
    _ensure_schema()
    rows = db.query(DirectAccount).all()
    return sorted(rows, key=lambda a: (_rank(a), a.login), reverse=True)


def suggest(db: Session, panel_domains: set[str]) -> dict[str, DirectAccount]:
    """Домен → кабинет, который стоит к нему привязать.

    На один домен может смотреть несколько кабинетов: заведённый дважды дубль,
    старый кабинет рядом с новым. Побеждает тот, через который идёт расход, —
    см. ``_rank``.
    """
    best: dict[str, DirectAccount] = {}
    for acc in listing(db):
        if acc.status != "ok" or not acc.domain or acc.domain not in panel_domains:
            continue
        cur = best.get(acc.domain)
        if cur is None or _rank(acc) > _rank(cur):
            best[acc.domain] = acc
    return best


def bind(domain: str, login: str) -> None:
    """Привязать кабинет к домену — это и есть то, что читает провайдер."""
    from app.credentials import set_cred

    set_cred(f"direct_login:{domain.strip()}", login.strip())


def unbind(domain: str) -> None:
    from app.credentials import set_cred

    set_cred(f"direct_login:{domain.strip()}", "")


def bound_logins(domains) -> dict[str, str]:
    """Текущие привязки домен→логин, чтобы показать их рядом с найденным."""
    from app.credentials import get_cred

    out: dict[str, str] = {}
    for domain in domains:
        value = (get_cred(f"direct_login:{domain}") or "").strip()
        if value:
            out[domain] = value
    return out


def bind_suggested(db: Session, panel_domains: set[str],
                   overwrite: bool = False) -> list[tuple[str, str]]:
    """Привязать всё найденное разом. Возвращает список пар (домен, логин).

    ``overwrite=False`` бережёт привязки, сделанные руками: у домена может быть
    свой отдельный токен и логин под него, и затирать это результатом разведки
    нельзя.
    """
    existing = bound_logins(panel_domains)
    by_login = {a.login: a for a in listing(db)}
    done: list[tuple[str, str]] = []
    for domain, acc in sorted(suggest(db, panel_domains).items()):
        if not overwrite and existing.get(domain):
            continue
        current = existing.get(domain)
        if current == acc.login:
            continue
        # Один кабинет бывает доступен под несколькими логинами-псевдонимами:
        # у основного кабинета это main-login и main-100003-r4v7,
        # ClientId у обоих 10000003. Данные они дают одни и те же, а победитель
        # среди равных решался алфавитом — привязка прыгала бы туда-сюда при
        # каждой разведке. Уже привязанный псевдоним оставляем как есть.
        prev = by_login.get(current) if current else None
        if prev is not None and prev.client_id and prev.client_id == acc.client_id:
            continue
        bind(domain, acc.login)
        done.append((domain, acc.login))
    return done
