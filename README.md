# Статистика из Яндекс Директа через API

Два независимых куска:

- **`direct_stats.py`** — скрипт выгрузки статистики Директа (Reports API v5) в CSV.
- **`panel-direct-tab/`** — вкладка «Директ» для панели SEO Статистики: история в БД,
  ночной сбор, разрезы, журнал изменений настроек, привязка рекламных кабинетов к доменам.
  Установка и состав — `panel-direct-tab/DEPLOY.md`.

## Быстрый старт по скрипту

```bash
pip install -r requirements.txt
cp .env.example .env        # вписать YANDEX_DIRECT_TOKEN
python direct_stats.py --date-range LAST_30_DAYS
```

Токен: [oauth.yandex.ru](https://oauth.yandex.ru/) → приложение с правом «Использование
API Яндекс Директа». Отдельно нужна одобренная заявка на доступ к API — до одобрения
боевой API отвечает ошибкой 58, работает только песочница
(`YANDEX_DIRECT_SANDBOX=1`).

Ключи скрипта: `--report-type`, `--fields`, `--date-range`, `--goals`, `--attribution`,
`--out`. Полный список — `python direct_stats.py --help`.

## Секреты

`.env` в `.gitignore` и в репозиторий не попадает. Токены, логины кабинетов и выгрузки
здесь не хранятся — только код.

## Подробности

Пошаговая инструкция «с нуля» (регистрация приложения, заявка, получение токена, разбор
ошибок, песочница, расписание) лежит на дропе: `direct-api-instrukciya.md`.

Официальная документация: [yandex.ru/dev/direct/](https://yandex.ru/dev/direct/)
