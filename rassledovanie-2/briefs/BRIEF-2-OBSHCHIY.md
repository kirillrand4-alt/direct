# Общий бриф всем агентам расследования‑2

Холдинг «Компрессор Центр», 22 сайта, продажа промышленных компрессоров, осушителей, азотных и кислородных
установок. Сайт enger‑air.ru (счётчик Метрики 91234386, GSC `sc-domain:enger-air.ru`, site_id 17 GSC / 37 ЯВМ)
живёт на Директе (91% визитов), органика Google даёт 1,5% визитов, но 14% лидов и 16,5% квал‑лидов.

Первое расследование (10–16.09.2026, 14 агентов) ответило: Google‑трафик enger упал на **−26% ≈ −270
кликов/мес**, 76% падения — потеря показов, 24% — CTR; объяснено 28%, **72% не объяснены**; премисы
владельца («позиция растёт» — артефакт `num=100`; «выпадает хвост» — выпала голова) опровергнуты; цена
вопроса 2–3 лида и <1 квал‑лида в месяц. Полный вердикт — `VERDIKT-enger-google-2026-09-15.md` на дропе,
методичка — `METODIKA-RASSLEDOVANIYA-2026-09-18.md`. Оба обязательны к прочтению до начала работы.

Второе расследование делает две вещи: **закрывает необъяснённые 72% SEO** (новые данные: URL Inspection,
дневная гранулярность, российская выдача Google из базы) и **впервые расследует Директ** — 70% квал‑лидов
холдинга и ≈14 млн ₽ за 90 дней.

---

## 1. Правила (дословно из методички, не обсуждаются)

1. Любое число — с указанием файла и фильтра, иначе не принимается.
2. Не выдумывать URL, запросы, цифры. Строки нет — так и писать.
3. Вес находки: доля от падения (в кликах), от лидов, от рублей. Находка без веса бесполезна.
4. Владелец приходит с готовым диагнозом, и он обычно неверен. Первым проверять формулировку вопроса.
5. По‑русски, сжато, таблицами. Без «важно отметить» и вводных абзацев.
6. Данные не редактировать. Скрипты и выгрузки — в свой `work/<id>/`, имена уникальные.
7. Отчёт обязателен даже при неудаче: что пробовал, что не вышло, почему.

## 2. Обязательный метод (методичка, раздел 7)

1. **Плацебо на всё**: сдвиг времени ±7/14/21 день, перестановка меток, случайная выборка того же объёма.
2. **Синтетический ноль**: мир, где эффекта заведомо нет, прогнать через свой оценщик.
3. **Обратный тест на регрессию к среднему**: эффект «упало у тех, кто…» померить назад во времени.
4. **Эталон**: 15 настоящих ClientID из Битрикса (`BITRIX-DEAL-20260828.zip` на дропе) — единственное
   место, где точность связывания измеряется, а не оценивается.
5. **Нормировка на длину месяца** (февраль 28, август 31; сентябрь 2026 — 17 дней).
6. **Вес находки** в кликах/лидах/рублях.
7. **Называть необъяснённую долю.**

## 3. Ловушки данных (методичка, раздел 6, дословно — не переоткрывать)

1. `num=100` отключён 12–14 сентября 2025. Сравнение позиции или CTR через сентябрь 2025 недействительно.
   Показы на позициях >20 схлопнулись 131 647 → 1 283.
2. Позиция GSC — средневзвешенная по показам. Растёт сама, когда отваливается хвост.
3. query‑экспорт GSC содержит 29–32% кликов свойства и 85% показов. Клики брать из page‑экспорта.
4. Яндекс.Вебмастер накручен ПФ: отбрасывать строки с CTR > 30%. 34–43% яндексовых кликов не доходят до
   Метрики (у Google 17–22%).
5. Метрика фильтрует роботов до выгрузки — `is_robot` пуст везде, это не поломка.
6. ClientID Метрики — доменная кука; межсайтовая склейка по client_id невозможна.
7. Выгрузка сделок Битрикса — строка на товарную позицию: 1 390 строк = 320 сделок. Дедуплицировать по `ID`.
8. Поле `YM Client ID` в Битриксе сломано; эталон — `Yandex Client Id B242YA`, 15 уникальных ID.
9. Метка roistat: `direct<N>_<search|context>_<id_кампании>_[группа_объявление_]<фраза>`; фраза — hex UTF‑8 со
   снятыми ведущими байтами D0/D1 (`0xB0–0xBF` → добавить `D0`, `0x80–0x8F` → `D1`). ID объявления всегда
   `rsMasterBanner` — неразличим.
10. Wordstat двоит формы (`enger` = `enger.`, `enger компрессор` = `компрессор enger`). Считать по базовым.
11. Классификация страниц по числу слешей врёт: 36% «карточек» — разделы. Использовать списки владельца
    `R2-projects-urls.csv.gz` (проекты 31 и 32 для enger) или живые HTTP‑запросы.
12. Офисный трафик: IP `93.189.221.xxx` и ещё шесть подсетей — 974 визита, все Барнаул. Вычитать.
13. Roistat видит 46–60% квал‑лидов, точность ~53%; только 31,4% квалов внутри «Все лиды».
14. Сопоставление сделок по времени не работает: лифт против плацебо ≈ 1,0×.

Добавлено разведкой 18.09:

15. В `visit.extra` цели лежат как JSON `goalsID`; в выгрузках `R2-*` уже вынесены в колонку `goals`
    (ID через запятую). Нет цели — пустая строка.
16. ID целей различаются по счётчикам, названия одинаковые. Брать из `R2-metrika-goals-all.csv` /
    `R2-metrika-goal-pick.json` (ключи qual, all, call, mail, deal, spam, b24q, b24n, form).
    Enger: квал 474844139, все лиды 474844140, коллтрекинг 474354415, емейлтрекинг 329243752.
    Prokompressor: квал 474843981, все лиды 474843983, коллтрекинг 471083619, емейлтрекинг 334022085.
17. Директ в панели считает конверсии **своей** атрибуцией (модель в колонке `attribution`, пусто = по
    умолчанию), Метрика — lastsign. Одна кампания даёт два разных числа квалов; расхождение — не ошибка,
    а предмет B8.
18. `direct_breakdown_daily.kind='query'` есть только с 2026‑03‑02, `criteria` — с 2025‑08‑20.
19. Параметр `attribution` API Метрики молча игнорируется, если dimensions начинаются с `ym:s:lastsign*`
    (методичка 5.6). В `R2-metrika-api-attribution-models.csv` dimension и параметр менялись согласованно.

## 4. Реперные числа (методичка, раздел 8) — «было». Колонку «стало» заполняет A1

| показатель | пик окт–ноя 2025 | июл–авг 2026 | стало (A1, до 2026‑09‑17) |
|---|---|---|---|
| клики GSC, свойство (page‑файл) | 2 257 | 1 441 | |
| показы GSC | 186 739 | 145 361 | |
| позиция средневзвешенная | 9,9 | 7,8 | |
| честная оценка падения | — | −26%, ≈ −270 кликов/мес (база сен‑25…апр‑26 = 1 018/мес, сейчас 749) | |
| разложение падения | — | 76% показы / 24% CTR | |
| объяснённая доля | — | 28% | |
| визиты Google‑органики в Метрике | 999 (окт‑25) | 597 (авг‑26) | |
| жёсткие лиды enger в месяц | 13,0 | 13,7 | |
| квалы холдинга, август 2026 | реклама 98 (64%) → 106 после перепривязки; Google 21 → 23; Яндекс 10 → 11; прочее 2; прямые/ссылки/внутр. 21 → 10 первый контакт; итого 152 | | |
| конверсия в квал | реклама 119 / 319 446 = 0,037%; органика 40 / 6 354 = 0,630% (×17) | | |
| Roistat против человека (prokompressor, июль) | 94 сделки, Roistat пометил 56%, точность ~53%, квалы внутри «Все лиды» 31,4% | | |
| связка Битрикс↔Метрика (320 сделок) | согласованно 27, жёсткий сигнал 13, вероятностно 120, спорные 14, не связаны 146 | | |

## 5. Каталог данных (дроп, префикс `R2-`, разделитель `;`, UTF‑8, gz)

Скачать: `curl -sS -H "X-Drop-Token: $DROP_TOKEN" "$DROP_URL/<имя>" -o data/<имя>`.

### SEO
| файл | колонки | примечание |
|---|---|---|
| `R2-enger-gsc-page-day.csv.gz` | date;url;clicks;impressions;position | GSC site 17, все страны, по дням, 2025‑02‑14…2026‑09‑17 |
| `R2-enger-gsc-query-day.csv.gz` | date;query;page_url;clicks;impressions;position | page_url может быть пустым |
| `R2-enger-gsc-page-device-day.csv.gz` | date;url;device;clicks;impressions;position | |
| `R2-enger-pages.csv.gz` | page_id;url;normalized_url;first_seen;last_seen | когда URL впервые/последний раз виден в GSC |
| `R2-enger-ywm-page-day.csv.gz`, `R2-enger-ywm-query-day.csv.gz` | как GSC | ЯВМ с 2026‑05‑24, ловушка 4 |
| `R2-enger-ywm-indexed-urls.csv.gz` | captured_on;url;title | индекс Яндекса по дням |
| `R2-holding-ywm-indexed-count.csv.gz` | site_id;property;captured_on;urls | |
| `R2-holding-site-day.csv.gz` | site_id;source;property;date;clicks;impressions;position | все 51 свойство |
| `R2-holding-site-device-day.csv.gz` | + device | |
| `R2-holding-page-month.csv.gz` | site_id;source;property;month;url;clicks;impressions;position | все сайты |
| `R2-holding-query-month.csv.gz` | site_id;source;property;month;query;… | без enger ЯВМ (он по дням) |
| `R2-serp-results.csv.gz` | keyword;se;region;position;url;url_domain;title;captured_on;task_id | region 1011969 = Google Москва, 213 = Яндекс Москва |
| `R2-gsc-property-day.csv.gz` | slice;date;clicks;impressions;position | slice: world, rus, rus‑DESKTOP/MOBILE/TABLET, image, video, news, discover |
| `R2-gsc-query-page-windows-rus.csv.gz` | window;query;page;clicks;impressions;position | окна 2025‑07‑08, 2025‑09, 2025‑12‑01, 2026‑02, 2026‑03‑04, 2026‑05‑06, 2026‑07‑08, 2026‑09; Россия, web |
| `R2-gsc-query-page-country-windows.csv.gz` | window;query;page;country;… | 2025‑06‑world, 2025‑10‑11‑world, 2026‑07‑08‑world |
| `R2-gsc-country-page-2025.csv.gz`, `R2-gsc-country-query-2025.csv.gz` | month;country;page/query;… | апр–сен 2025 |
| `R2-gsc-search-appearance-month.csv` | month;appearance;clicks;impressions;position | |
| `R2-gsc-inspect-enger.csv` (+ `.jsonl.gz` полный ответ) | url;peak_impr;now_impr;peak_clicks;now_clicks;verdict;coverage_state;robots_state;indexing_state;last_crawl;fetch_state;google_canonical;user_canonical;crawled_as;sitemaps;referring_urls_n;mobile_verdict;rich_verdict;error | ~420 URL: все с ≥2 кликами на пике и нулём показов сейчас + топ по показам |
| `R2-gsc-sitemaps.json` | ответ API | |
| `R2-projects-urls.csv.gz` | project_id;project_name;site_id;favorite_goals;url | списки владельца; enger: 31, 32, 8, 25 |
| `R2-wordstat-history.csv.gz` | date;query;region;device;match_type;value;captured_at | |
| `R2-wordstat-series.csv.gz` | date;query;region;device;granularity;match_type;value | по дням/неделям |
| `R2-collection-run.csv.gz` | журнал сборов панели | где были дыры в сборе |
| `R2-url-brand.csv.gz` | domain;url_key;brand | |
| прошлые: `enger-gsc-dimensions-month.csv.gz`, `enger-gsc-query-page-windows.csv.gz` | см. `ENGER-RASSLEDOVANIE-2026-09-15.tgz/BRIEF.md` | |

### Метрика
| файл | колонки | примечание |
|---|---|---|
| `R2-holding-visits-month.csv.gz` | counter_id;month;traffic_source;search_engine;adv_engine;device;visits;visits_with_goals;bounces;page_views;duration | все 22 счётчика, 2025‑06…2026‑09‑17 |
| `R2-holding-visits-nonad.csv.gz` | counter_id;date;date_time;client_id;visit_id;traffic_source;search_engine;adv_engine;referer;start_url;end_url;page_views;duration;bounce;device;os;browser;region_city;ip;goals | все нерекламные визиты построчно |
| `R2-holding-visits-goals.csv.gz` | те же колонки | все визиты с ≥1 целью, включая рекламу |
| `R2-holding-ad-campaign-month.csv.gz` | counter_id;month;campaign_id;src_type;roistat_n;adv_engine;device;visits;clients;bounces;page_views;duration | кампания из метки roistat; src_type search/context/utm; пусто = метки нет |
| `R2-holding-ad-campaign-goal-month.csv.gz` | counter_id;month;campaign_id;src_type;goal_id;visits_with_goal | |
| `R2-ad-visits-slim-enger-pk.csv.gz` | counter_id;date_time;client_id;visit_id;campaign_id;src_type;start_path;page_views;duration;bounce;device;region_city;goals | рекламные визиты enger и prokompressor построчно |
| `R2-holding-ai-referrer-visits.csv.gz` | как visits‑nonad | реферер из ИИ‑ассистента (perplexity, alice, chatgpt, gemini, deepseek, gigachat, copilot, нейро…) |
| `R2-holding-referrer-host-month.csv.gz` | counter_id;month;traffic_source;referer_host;visits;visits_with_goals | |
| `R2-enger-visits-full.csv.gz` | как visits‑nonad | все 645 068 визитов enger до 09‑17 |
| `R2-hit-utm-month.csv.gz` | counter_id;month;traffic_source;utm_source;utm_medium;utm_campaign;utm_content;hits;clients | utm_content = объявление\|кампания\|устройство |
| `R2-metrika-goals-all.csv` | counter_id;domain;goal_id;name;type;is_retargeting;default_price;is_favorite;cond_url | |
| `R2-metrika-api-source-month.csv.gz` | counter_id;domain;month;traffic_source;search_engine;adv_engine;visits;users;bounce_rate;page_depth;avg_duration;g_qual;g_all;g_call;g_mail;g_deal;g_spam;g_b24q;g_b24n;g_form | API, lastsign |
| `R2-metrika-api-direct-campaign-month.csv.gz` | counter_id;domain;month;campaign_id;campaign_name;platform_type;… те же метрики | квалы по кампаниям Директа по атрибуции Метрики |
| `R2-metrika-api-attribution-models.csv` | counter_id;domain;dim_prefix;attribution;traffic_source;search_engine;… | июн–авг 2026, 8 счётчиков, 8 моделей |
| `R2-metrika-api-direct-phrase-month.csv.gz` | counter_id;domain;month;campaign_id;search_phrase;… | enger, prokompressor, с 2026‑03 |
| прошлые: `holding-goals-wide.csv.gz` (20.06–10.08), `pk-july-goals.csv.gz`, `SVYAZKA-…`, `SDELKI-…`, `KVALY-SIROTY-…`, `BITRIX-DEAL-20260828.zip` | | |

### Директ (панель)
| файл | колонки | примечание |
|---|---|---|
| `R2-direct-config.json` | наборы целей и разрезы по доменам | какие ID целей стоят за goal_key «Квал», «Лид 1» |
| `R2-direct-daily.csv.gz` | domain;date;goal_key;attribution;impressions;clicks;cost;conversions | cost в рублях без НДС (проверить по direct-config/DEPLOY) |
| `R2-direct-campaign-daily.csv.gz` | domain;date;campaign_id;campaign_name;goal_key;attribution;… | с 2026‑03‑01 |
| `R2-direct-breakdown-daily.csv.gz` | domain;date;kind;key_id;key_text;campaign_id;goal_key;attribution;impressions;clicks;cost;conversions | kind: query / criteria / device |
| `R2-direct-settings-snapshot.csv.gz` | domain;object_type;object_id;object_name;updated_at;payload | payload — JSON настроек (Type кампании, стратегия, минус‑слова) |
| `R2-direct-change.csv.gz` | журнал изменений с 08.2026 | |
| `R2-direct-collect-run.csv.gz`, `R2-direct-account.csv.gz` | | |
| `DIREKT-final.json` (прошлое) | сводка кампаний prokompressor | |

## 6. Доступ к серверу (методичка 5.2–5.6)

`tools/wrap.py inner_X.py out_X.txt <таймаут>` → `w_inner_X.py`; `tools/push.py w_inner_X.py --timeout <N>`
(только `--timeout`, второй позиционный аргумент ломает скрипт). Вывод длиннее ~4 000 символов забирать с дропа
(`out_X.txt`) отдельным curl. Скрипт исполняется питоном панели (pandas есть), база только `?mode=ro`.
Живые HTTP‑запросы к enger‑air.ru и другим сайтам холдинга разрешены (из песочницы или с сервера).
Google/Яндекс скрейпить из песочницы не пытаться — не работает, зря потраченные часы прошлого захода.

## 7. Формат отчёта `work/<id>/REPORT-<id>.md`

```
# <id>: <название>
## Ответ в трёх строках
## Таблица утверждений: № | утверждение | число | файл | фильтр/скрипт | вес (клики/лиды/₽ и доля) | уверенность (высокая/средняя/низкая) | плацебо/контроль
## Что опровергнуто (из брифа или своих же первых версий)
## Чего не удалось и почему
## Скрипты (список файлов в work/<id>/ с одной строкой «что делает»)
```
