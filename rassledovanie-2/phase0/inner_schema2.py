# -*- coding: utf-8 -*-
# Разведка неожиданных таблиц (serp_result, indexed_url_snapshot, hit, direct_account, direct_change, project).
# Отработал 18.09.2026, вывод — out_schema2_2026-09-18.txt на дропе.
import sqlite3
con = sqlite3.connect("file:C:/seostat/data/seo.db?mode=ro", uri=True)
q = lambda s, *a: con.execute(s, a).fetchall()
for t in ("serp_result", "serp_task", "indexed_url_snapshot", "hit", "device_metric_daily", "site_device_daily",
          "site_total_daily", "project", "project_url", "donor_site", "url_brand", "wordstat_series", "collection_run",
          "call_company", "direct_account", "direct_change", "direct_settings_snapshot"):
    r = q("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", t)
    if r:
        print("\n-- %s\n%s" % (t, r[0][0][:1400]))
print("\n=== serp_result: примеры и диапазон ===")
cols = [c[1] for c in q("PRAGMA table_info(serp_result)")]
print("cols:", cols)
for r in q("SELECT * FROM serp_result ORDER BY id DESC LIMIT 5"):
    print("  ", [str(x)[:80] for x in r])
for c in cols:
    if any(k in c.lower() for k in ("date", "engine", "source", "site", "region", "device", "query")):
        print("   %s: " % c, q("SELECT %s, COUNT(*) FROM serp_result GROUP BY 1 ORDER BY 2 DESC LIMIT 12" % c))
print("\n=== indexed_url_snapshot: примеры ===")
cols = [c[1] for c in q("PRAGMA table_info(indexed_url_snapshot)")]
print("cols:", cols)
for r in q("SELECT * FROM indexed_url_snapshot ORDER BY id DESC LIMIT 3"):
    print("  ", [str(x)[:100] for x in r])
print("   site_id: ", q("SELECT site_id, COUNT(*) FROM indexed_url_snapshot GROUP BY 1 ORDER BY 2 DESC LIMIT 12"))
print("\n=== hit: примеры ===")
cols = [c[1] for c in q("PRAGMA table_info(hit)")]
print("cols:", cols)
for r in q("SELECT * FROM hit ORDER BY id DESC LIMIT 3"):
    print("  ", [str(x)[:100] for x in r])
print("   по счётчику:", q("SELECT counter_id, COUNT(*), MIN(date), MAX(date) FROM hit GROUP BY 1 ORDER BY 2 DESC LIMIT 25"))
print("\n=== project / project_url ===")
for r in q("SELECT * FROM project LIMIT 40"):
    print("  ", [str(x)[:60] for x in r])
print("  project_url по проекту:", q("SELECT project_id, COUNT(*) FROM project_url GROUP BY 1 ORDER BY 2 DESC LIMIT 40"))
print("\n=== direct_account ===")
cols = [c[1] for c in q("PRAGMA table_info(direct_account)")]
print("cols:", cols)
for r in q("SELECT * FROM direct_account"):
    print("  ", [str(x)[:50] for x in r])
print("\n=== direct_change (последние 20) ===")
for r in q("SELECT * FROM direct_change ORDER BY id DESC LIMIT 20"):
    print("  ", [str(x)[:70] for x in r])
con.close()
print("готово")
