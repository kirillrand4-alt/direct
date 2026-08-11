#!/usr/bin/env python3
"""Выгрузка статистики из Яндекс Директа (API v5, сервис Reports) в CSV.

Примеры:
    python direct_stats.py --date-from 2026-08-01 --date-to 2026-08-10 --out stats.csv
    python direct_stats.py --last-days 14 --report-type CRITERIA_PERFORMANCE_REPORT
    python direct_stats.py --date-range LAST_30_DAYS --goals 123456789

Токен берётся из переменной окружения YANDEX_DIRECT_TOKEN или из файла .env
рядом со скриптом. Как его получить — см. README.md.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

import requests

API_URL = "https://api.direct.yandex.com/json/v5/reports"
SANDBOX_URL = "https://api-sandbox.direct.yandex.com/json/v5/reports"

# Сколько ждать готовности отчёта, если Директ не прислал заголовок retryIn.
DEFAULT_RETRY_SECONDS = 10
# Потолок ожидания отчёта, чтобы скрипт не висел вечно.
MAX_WAIT_SECONDS = 15 * 60

# Наборы полей по умолчанию для каждого типа отчёта.
DEFAULT_FIELDS = {
    "ACCOUNT_PERFORMANCE_REPORT": [
        "Date", "Impressions", "Clicks", "Cost", "Ctr", "AvgCpc",
    ],
    "CAMPAIGN_PERFORMANCE_REPORT": [
        "Date", "CampaignId", "CampaignName", "Impressions", "Clicks",
        "Cost", "Ctr", "AvgCpc",
    ],
    "ADGROUP_PERFORMANCE_REPORT": [
        "Date", "CampaignName", "AdGroupId", "AdGroupName", "Impressions",
        "Clicks", "Cost", "Ctr", "AvgCpc",
    ],
    "AD_PERFORMANCE_REPORT": [
        "Date", "CampaignName", "AdGroupName", "AdId", "Impressions",
        "Clicks", "Cost", "Ctr", "AvgCpc",
    ],
    "CRITERIA_PERFORMANCE_REPORT": [
        "Date", "CampaignName", "AdGroupName", "CriterionId", "Criterion",
        "Impressions", "Clicks", "Cost", "Ctr", "AvgCpc",
    ],
    "SEARCH_QUERY_PERFORMANCE_REPORT": [
        "Date", "CampaignName", "Query", "Impressions", "Clicks", "Cost", "Ctr",
    ],
}


def load_dotenv(path: Path) -> None:
    """Минимальный .env-ридер: KEY=VALUE, строки с # игнорируются.

    Переменные, уже заданные в окружении, имеют приоритет над файлом.
    """
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def build_report_definition(args) -> dict:
    """Собирает тело запроса к сервису Reports."""
    selection: dict = {}
    if args.date_range == "CUSTOM_DATE":
        selection["DateFrom"] = args.date_from
        selection["DateTo"] = args.date_to

    fields = (
        [f.strip() for f in args.fields.split(",") if f.strip()]
        if args.fields
        else DEFAULT_FIELDS.get(args.report_type, DEFAULT_FIELDS["CAMPAIGN_PERFORMANCE_REPORT"])
    )

    params: dict = {
        "SelectionCriteria": selection,
        "FieldNames": fields,
        # Имя обязано быть уникальным: Директ кеширует отчёты по имени и на повтор
        # с теми же именем, но другими параметрами отвечает ошибкой.
        "ReportName": f"{args.report_type}_{int(time.time() * 1000)}",
        "ReportType": args.report_type,
        "DateRangeType": args.date_range,
        "Format": "TSV",
        "IncludeVAT": "YES" if args.include_vat else "NO",
    }

    if args.goals:
        params["Goals"] = [g.strip() for g in args.goals.split(",") if g.strip()]
        if args.attribution:
            params["AttributionModels"] = [
                a.strip() for a in args.attribution.split(",") if a.strip()
            ]

    return {"params": params}


def build_headers(token: str, client_login: str | None, processing_mode: str) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept-Language": "ru",
        "Content-Type": "application/json; charset=utf-8",
        "processingMode": processing_mode,
        # Без этого все денежные значения приходят умноженными на 1 000 000.
        "returnMoneyInMicros": "false",
        "skipReportHeader": "true",
        "skipReportSummary": "true",
    }
    if client_login:
        # Обязателен для агентских аккаунтов, рекламодателю не нужен.
        headers["Client-Login"] = client_login
    return headers


def describe_api_error(response: requests.Response) -> str:
    try:
        error = response.json().get("error", {})
    except ValueError:
        return response.text.strip()[:500]
    parts = [
        error.get("error_string"),
        error.get("error_detail"),
        f"код {error['error_code']}" if error.get("error_code") else None,
        f"request_id {error['request_id']}" if error.get("request_id") else None,
    ]
    return " | ".join(p for p in parts if p)


def fetch_report(url: str, body: dict, headers: dict, verbose: bool) -> tuple[str, str | None]:
    """Запрашивает отчёт, дожидаясь готовности. Возвращает (TSV, заголовок Units)."""
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    waited = 0

    while True:
        response = requests.post(url, data=payload, headers=headers, timeout=300)
        # Директ отдаёт TSV в UTF-8, но не всегда объявляет кодировку.
        response.encoding = "utf-8"

        if response.status_code == 200:
            return response.text, response.headers.get("Units")

        if response.status_code in (201, 202):
            retry_in = int(response.headers.get("retryIn", DEFAULT_RETRY_SECONDS))
            waited += retry_in
            if waited > MAX_WAIT_SECONDS:
                raise TimeoutError(
                    f"Отчёт не готов за {MAX_WAIT_SECONDS // 60} мин. "
                    "Сузьте период или набор полей."
                )
            if verbose:
                print(f"Отчёт в очереди, повтор через {retry_in} с...", file=sys.stderr)
            time.sleep(retry_in)
            continue

        if response.status_code == 400:
            raise RuntimeError(f"Ошибка в запросе: {describe_api_error(response)}")

        if response.status_code == 401:
            raise RuntimeError(
                "Токен не принят. Проверьте, что он не истёк и что в приложении "
                "отмечено право «Использование API Яндекс Директа» (direct:api)."
            )

        if response.status_code >= 500:
            raise RuntimeError(
                f"Ошибка на стороне Яндекса (HTTP {response.status_code}). "
                f"Повторите позже. {describe_api_error(response)}"
            )

        raise RuntimeError(f"HTTP {response.status_code}: {describe_api_error(response)}")


def tsv_to_csv(tsv: str, out_path: Path) -> int:
    """Пишет TSV-ответ в CSV. Возвращает число строк данных (без заголовка)."""
    rows = [line.split("\t") for line in tsv.splitlines() if line.strip()]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8-sig", newline="") as fh:
        csv.writer(fh, delimiter=";").writerows(rows)
    return max(len(rows) - 1, 0)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Выгрузка статистики Яндекс Директа в CSV.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--date-from", help="Начало периода, YYYY-MM-DD")
    parser.add_argument("--date-to", help="Конец периода, YYYY-MM-DD")
    parser.add_argument(
        "--last-days",
        type=int,
        help="Вместо явных дат: последние N дней, включая сегодня",
    )
    parser.add_argument(
        "--date-range",
        default="CUSTOM_DATE",
        help="Готовый период: TODAY, YESTERDAY, LAST_7_DAYS, LAST_30_DAYS, "
        "THIS_MONTH, LAST_MONTH, THIS_YEAR, ALL_TIME, AUTO",
    )
    parser.add_argument(
        "--report-type",
        default="CAMPAIGN_PERFORMANCE_REPORT",
        help="Тип отчёта, см. README",
    )
    parser.add_argument(
        "--fields",
        help="Поля через запятую. По умолчанию — набор под выбранный тип отчёта",
    )
    parser.add_argument("--goals", help="ID целей Метрики через запятую")
    parser.add_argument(
        "--attribution",
        help="Модели атрибуции через запятую: LC, FC, LSC, LYDC, AUTO",
    )
    parser.add_argument(
        "--client-login",
        default=os.environ.get("YANDEX_DIRECT_CLIENT_LOGIN"),
        help="Логин клиента — только для агентских аккаунтов",
    )
    parser.add_argument("--out", default="stats.csv", help="Куда сохранить CSV")
    parser.add_argument(
        "--processing-mode",
        default="auto",
        choices=["auto", "online", "offline"],
        help="Режим формирования отчёта",
    )
    parser.add_argument(
        "--no-vat",
        dest="include_vat",
        action="store_false",
        help="Суммы без НДС (по умолчанию — с НДС)",
    )
    parser.add_argument(
        "--sandbox",
        action="store_true",
        default=os.environ.get("YANDEX_DIRECT_SANDBOX") == "1",
        help="Работать с песочницей вместо боевого API",
    )
    parser.add_argument("--quiet", action="store_true", help="Меньше сообщений")
    return parser.parse_args(argv)


def resolve_dates(args) -> None:
    """Приводит аргументы дат к тому, что ждёт API, и валидирует их."""
    if args.last_days:
        today = dt.date.today()
        args.date_from = (today - dt.timedelta(days=args.last_days - 1)).isoformat()
        args.date_to = today.isoformat()
        args.date_range = "CUSTOM_DATE"

    if args.date_range != "CUSTOM_DATE":
        return

    if not (args.date_from and args.date_to):
        raise SystemExit(
            "Укажите период: --date-from и --date-to, либо --last-days N, "
            "либо готовый --date-range (например LAST_30_DAYS)."
        )

    for value in (args.date_from, args.date_to):
        try:
            dt.date.fromisoformat(value)
        except ValueError:
            raise SystemExit(f"Дата «{value}» не в формате YYYY-MM-DD.")

    if args.date_from > args.date_to:
        raise SystemExit("--date-from позже, чем --date-to.")


def main(argv=None) -> int:
    load_dotenv(Path(__file__).resolve().parent / ".env")
    args = parse_args(argv)
    resolve_dates(args)

    token = os.environ.get("YANDEX_DIRECT_TOKEN")
    if not token:
        raise SystemExit(
            "Не задан YANDEX_DIRECT_TOKEN. Положите токен в .env "
            "(см. .env.example) или в переменную окружения. Как получить — README.md, шаг 3."
        )

    if args.client_login is None:
        args.client_login = os.environ.get("YANDEX_DIRECT_CLIENT_LOGIN")

    url = SANDBOX_URL if args.sandbox else API_URL
    verbose = not args.quiet

    if verbose:
        where = "песочница" if args.sandbox else "боевой API"
        period = (
            f"{args.date_from}..{args.date_to}"
            if args.date_range == "CUSTOM_DATE"
            else args.date_range
        )
        print(f"Запрашиваю {args.report_type} за {period} ({where})...")

    tsv, units = fetch_report(
        url,
        build_report_definition(args),
        build_headers(token, args.client_login, args.processing_mode),
        verbose,
    )

    out_path = Path(args.out)
    row_count = tsv_to_csv(tsv, out_path)

    if verbose:
        print(f"Отчёт готов. Строк: {row_count}")
        if units:
            spent, left, limit = (units.split("/") + ["?", "?", "?"])[:3]
            print(f"Баллы: потрачено {spent}, осталось {left} из {limit}")
        if row_count == 0:
            print(
                "Данных нет. Проверьте период, а для агентского аккаунта — "
                "--client-login.",
                file=sys.stderr,
            )
        print(f"Сохранено: {out_path}")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, TimeoutError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        sys.exit(1)
    except requests.RequestException as exc:
        print(f"Сеть недоступна или API не отвечает: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
