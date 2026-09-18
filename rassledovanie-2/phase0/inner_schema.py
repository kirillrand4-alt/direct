# -*- coding: utf-8 -*-
# Разведка схемы базы панели (отработал 18.09.2026, вывод — out_schema_2026-09-18.txt на дропе).
import sqlite3, sys, importlib, os, platform, time
con = sqlite3.connect("file:C:/seostat/data/seo.db?mode=ro", uri=True)
q = lambda s, *a: con.execute(s, a).fetchall()
print("время сервера:", time.strftime("%Y-%m-%d %H:%M:%S"), "| tz offset:", time.strftime("%z"), "| host:", platform.node())
print("\n=== ТАБЛИЦЫ И СТРОКИ ===")
for (t,) in q("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
    try:
        n = q("SELECT COUNT(*) FROM \"%s\"" % t)[0][0]
    except Exception as e:
        n = "err %s" % e
    print("   %-32s %10s" % (t, n))
print("\n=== СХЕМЫ КЛЮЧЕВЫХ ТАБЛИЦ ===")
for t in ("site", "source", "page", "query", "page_metric_daily", "query_metric_daily", "direct_daily",
          "direct_campaign_daily", "direct_breakdown_daily", "direct_collect_run", "direct_account",
          "direct_change", "direct_setting_snapshot", "wordstat_history", "url_brand", "visit"):
    r = q("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", t)
    if r:
        print("\n-- %s\n%s" % (t, r[0][0][:1500]))
print("\n=== САЙТЫ ===")
for r in q("SELECT s.id, s.property_uri, src.code FROM site s LEFT JOIN source src ON src.id=s.source_id ORDER BY s.id"):
    print("   ", r)
print("\n=== page_metric_daily: диапазон по сайту ===")
for r in q("SELECT site_id, MIN(date), MAX(date), COUNT(*), SUM(clicks) FROM page_metric_daily GROUP BY site_id ORDER BY site_id"):
    print("   ", r)
print("\n=== query_metric_daily: диапазон по сайту ===")
for r in q("SELECT site_id, MIN(date), MAX(date), COUNT(*), SUM(clicks) FROM query_metric_daily GROUP BY site_id ORDER BY site_id"):
    print("   ", r)
for t in ("direct_daily", "direct_campaign_daily", "direct_breakdown_daily"):
    print("\n=== %s: домен × goal_key × attribution ===" % t)
    try:
        for r in q("SELECT domain, goal_key, attribution, MIN(date), MAX(date), COUNT(*), SUM(clicks), ROUND(SUM(cost)), SUM(conversions) FROM \"%s\" GROUP BY 1,2,3 ORDER BY 1,2,3" % t):
            print("   ", r)
    except Exception as e:
        print("   err", e)
print("\n=== direct_breakdown_daily: kind × домен ===")
try:
    for r in q("SELECT domain, kind, COUNT(*), MIN(date), MAX(date) FROM direct_breakdown_daily GROUP BY 1,2 ORDER BY 1,2"):
        print("   ", r)
except Exception as e:
    print("   err", e)
print("\n=== direct_collect_run (последние 15) ===")
try:
    cols = [c[1] for c in q("PRAGMA table_info(direct_collect_run)")]
    print("   cols:", cols)
    for r in q("SELECT * FROM direct_collect_run ORDER BY id DESC LIMIT 15"):
        print("   ", r)
except Exception as e:
    print("   err", e)
print("\n=== visit: по счётчику ===")
for r in q("SELECT counter_id, COUNT(*), MIN(date), MAX(date) FROM visit GROUP BY counter_id ORDER BY 2 DESC"):
    print("   ", r)
print("\n=== app_setting: ключи (без значений) ===")
for (k,) in q("SELECT key FROM app_setting ORDER BY key"):
    print("   ", k)
print("\n=== python-пакеты на сервере ===")
for m in ("pandas", "numpy", "requests", "bs4", "lxml", "playwright", "selenium", "httpx", "cryptography",
          "googleapiclient", "google.oauth2", "openpyxl", "scipy", "sklearn", "PIL", "curl_cffi", "cloudscraper", "aiohttp", "yaml"):
    try:
        mod = importlib.import_module(m)
        print("   OK   %-16s %s" % (m, getattr(mod, "__version__", "")))
    except Exception as e:
        print("   --   %-16s %s" % (m, str(e)[:60]))
print("\n=== браузеры/плейрайт на диске ===")
for p in (r"C:\Program Files\Google\Chrome\Application\chrome.exe", r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
          r"C:\Program Files\Mozilla Firefox\firefox.exe", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
          os.path.expandvars(r"%LOCALAPPDATA%\ms-playwright"), os.path.expandvars(r"%USERPROFILE%\AppData\Local\ms-playwright"),
          r"C:\Users\Administrator\AppData\Local\ms-playwright"):
    print("   %-70s %s" % (p, os.path.exists(p)))
con.close()
print("готово")
