# -*- coding: utf-8 -*-
"""D1: выгрузка поисковых данных (GSC/ЯВМ/SERP/вордстат) на дроп, префикс R2-."""
import sqlite3, csv, gzip, io, os, re, json, time, tempfile, urllib.request, collections
DROP_URL = os.environ.get("DROP_URL", "https://parsercompressor.online/drop").rstrip("/")
TOK = os.environ.get("DROP_TOKEN", "")
if not TOK:
    for ln in open(r"C:\sender\server\runner-secrets.env", encoding="utf-8", errors="replace"):
        m = re.match(r"\s*DROP_TOKEN\s*=\s*(\S+)", ln)
        if m:
            TOK = m.group(1)
con = sqlite3.connect("file:C:/seostat/data/seo.db?mode=ro", uri=True)
con.execute("PRAGMA temp_store=MEMORY")
q = lambda s, *a: con.execute(s, a).fetchall()
T0 = time.time()


def put_bytes(name, blob):
    err = None
    for attempt in range(3):
        try:
            r = urllib.request.Request("%s/%s" % (DROP_URL, name), data=blob, method="PUT",
                                       headers={"X-Drop-Token": TOK})
            urllib.request.urlopen(r, timeout=600).read()
            return True
        except Exception as e:
            err = e
            time.sleep(5)
    print("   !! не выгружен %s: %s" % (name, str(err)[:100]))
    return False


def dump_csv(name, header, rows_iter):
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".gz")
    tmp.close()
    n = 0
    with gzip.open(tmp.name, "wt", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter=";", lineterminator="\n")
        w.writerow(header)
        for r in rows_iter:
            w.writerow(r)
            n += 1
    blob = open(tmp.name, "rb").read()
    os.unlink(tmp.name)
    ok = put_bytes(name, blob)
    print("   %-52s %9d строк %10d байт gz %s  [%ds]" % (name, n, len(blob), "OK" if ok else "FAIL", time.time() - T0), flush=True)
    return n


SITE_GSC, SITE_YWM = 17, 37
R2 = lambda p: round(p, 2) if p is not None else ""

print("=== serp_result: captured_on × se × region (строк, ключей) ===")
for r in q("SELECT captured_on, se, region, COUNT(*), COUNT(DISTINCT keyword) FROM serp_result GROUP BY 1,2,3 ORDER BY 1,2,3"):
    print("   ", r)
print("\n=== serp_result: ключи по теме (примеры, до 60) ===")
for r in q("""SELECT keyword, COUNT(DISTINCT captured_on), MIN(captured_on), MAX(captured_on)
              FROM serp_result WHERE keyword LIKE '%компрессор%' OR keyword LIKE '%осушител%' OR keyword LIKE '%азот%'
                 OR keyword LIKE '%enger%' OR keyword LIKE '%энгер%' GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 60"""):
    print("   ", r)
print("\n=== serp_result: строки с enger-air.ru ===")
for r in q("SELECT se, region, captured_on, COUNT(*), MIN(position), ROUND(AVG(position),2) FROM serp_result WHERE url_domain LIKE '%enger-air%' GROUP BY 1,2,3 ORDER BY 3"):
    print("   ", r)
print("\n=== serp_task (20 последних) ===")
for r in q("SELECT * FROM serp_task ORDER BY id DESC LIMIT 20"):
    print("   ", r)
print("\n=== query_metric_daily site17: строк / с page_id ===", q("SELECT COUNT(*), SUM(page_id IS NOT NULL) FROM query_metric_daily WHERE site_id=?", SITE_GSC))
print("=== query_metric_daily site37: строк / с page_id ===", q("SELECT COUNT(*), SUM(page_id IS NOT NULL) FROM query_metric_daily WHERE site_id=?", SITE_YWM))
print("=== indexed_url_snapshot: сайт × число снимков × даты ===")
for r in q("SELECT site_id, COUNT(DISTINCT captured_on), MIN(captured_on), MAX(captured_on), COUNT(*) FROM indexed_url_snapshot GROUP BY 1 ORDER BY 1"):
    print("   ", r)
print("\n=== collection_run: последние 12 ===")
for r in q("SELECT * FROM collection_run ORDER BY id DESC LIMIT 12"):
    print("   ", r)
print("", flush=True)

# ---- enger GSC
dump_csv("R2-enger-gsc-page-day.csv.gz", ["date", "url", "clicks", "impressions", "position"],
         ((d, u, c, i, R2(p)) for d, u, c, i, p in con.execute(
             "SELECT m.date, p.url, m.clicks, m.impressions, m.position FROM page_metric_daily m JOIN page p ON p.id=m.page_id WHERE m.site_id=? ORDER BY m.date, p.url", (SITE_GSC,))))
dump_csv("R2-enger-gsc-query-day.csv.gz", ["date", "query", "page_url", "clicks", "impressions", "position"],
         ((d, t, u or "", c, i, R2(p)) for d, t, u, c, i, p in con.execute(
             "SELECT m.date, qq.text, p.url, m.clicks, m.impressions, m.position FROM query_metric_daily m JOIN query qq ON qq.id=m.query_id LEFT JOIN page p ON p.id=m.page_id WHERE m.site_id=? ORDER BY m.date", (SITE_GSC,))))
dump_csv("R2-enger-gsc-page-device-day.csv.gz", ["date", "url", "device", "clicks", "impressions", "position"],
         ((d, u, dv, c, i, R2(p)) for d, u, dv, c, i, p in con.execute(
             "SELECT m.date, p.url, m.device, m.clicks, m.impressions, m.position FROM device_metric_daily m JOIN page p ON p.id=m.page_id WHERE m.site_id=? ORDER BY m.date", (SITE_GSC,))))
dump_csv("R2-enger-pages.csv.gz", ["page_id", "url", "normalized_url", "first_seen", "last_seen"],
         con.execute("SELECT id, url, normalized_url, first_seen, last_seen FROM page WHERE site_id=? ORDER BY id", (SITE_GSC,)))
# ---- enger ЯВМ
dump_csv("R2-enger-ywm-page-day.csv.gz", ["date", "url", "clicks", "impressions", "position"],
         ((d, u, c, i, R2(p)) for d, u, c, i, p in con.execute(
             "SELECT m.date, p.url, m.clicks, m.impressions, m.position FROM page_metric_daily m JOIN page p ON p.id=m.page_id WHERE m.site_id=? ORDER BY m.date, p.url", (SITE_YWM,))))
dump_csv("R2-enger-ywm-query-day.csv.gz", ["date", "query", "page_url", "clicks", "impressions", "position"],
         ((d, t, u or "", c, i, R2(p)) for d, t, u, c, i, p in con.execute(
             "SELECT m.date, qq.text, p.url, m.clicks, m.impressions, m.position FROM query_metric_daily m JOIN query qq ON qq.id=m.query_id LEFT JOIN page p ON p.id=m.page_id WHERE m.site_id=? ORDER BY m.date", (SITE_YWM,))))
dump_csv("R2-enger-ywm-indexed-urls.csv.gz", ["captured_on", "url", "title"],
         con.execute("SELECT captured_on, url, title FROM indexed_url_snapshot WHERE site_id=? ORDER BY captured_on, url", (SITE_YWM,)))
dump_csv("R2-holding-ywm-indexed-count.csv.gz", ["site_id", "property", "captured_on", "urls"],
         con.execute("SELECT i.site_id, s.property_uri, i.captured_on, COUNT(*) FROM indexed_url_snapshot i JOIN site s ON s.id=i.site_id GROUP BY 1,2,3 ORDER BY 1,3"))
# ---- холдинг: все сайты, итоги по дням и страницы по месяцам
dump_csv("R2-holding-site-day.csv.gz", ["site_id", "source", "property", "date", "clicks", "impressions", "position"],
         ((sid, src, prop, d, c, i, R2(p)) for sid, src, prop, d, c, i, p in con.execute(
             "SELECT t.site_id, src.code, s.property_uri, t.date, t.clicks, t.impressions, t.position FROM site_total_daily t JOIN site s ON s.id=t.site_id JOIN source src ON src.id=s.source_id ORDER BY t.site_id, t.date")))
dump_csv("R2-holding-site-device-day.csv.gz", ["site_id", "source", "property", "date", "device", "clicks", "impressions", "position"],
         ((sid, src, prop, d, dv, c, i, R2(p)) for sid, src, prop, d, dv, c, i, p in con.execute(
             "SELECT t.site_id, src.code, s.property_uri, t.date, t.device, t.clicks, t.impressions, t.position FROM site_device_daily t JOIN site s ON s.id=t.site_id JOIN source src ON src.id=s.source_id ORDER BY t.site_id, t.date")))
dump_csv("R2-holding-page-month.csv.gz", ["site_id", "source", "property", "month", "url", "clicks", "impressions", "position"],
         ((sid, src, prop, mo, u, c, i, round((pw or 0) / max(1, i or 1), 2)) for sid, src, prop, mo, u, c, i, pw in con.execute(
             """SELECT m.site_id, src.code, s.property_uri, substr(m.date,1,7), p.url, SUM(m.clicks), SUM(m.impressions), SUM(m.position*m.impressions)
                FROM page_metric_daily m JOIN page p ON p.id=m.page_id JOIN site s ON s.id=m.site_id JOIN source src ON src.id=s.source_id
                GROUP BY 1,2,3,4,5 ORDER BY 1,4""")))
dump_csv("R2-holding-query-month.csv.gz", ["site_id", "source", "property", "month", "query", "clicks", "impressions", "position"],
         ((sid, src, prop, mo, t, c, i, round((pw or 0) / max(1, i or 1), 2)) for sid, src, prop, mo, t, c, i, pw in con.execute(
             """SELECT m.site_id, src.code, s.property_uri, substr(m.date,1,7), qq.text, SUM(m.clicks), SUM(m.impressions), SUM(m.position*m.impressions)
                FROM query_metric_daily m JOIN query qq ON qq.id=m.query_id JOIN site s ON s.id=m.site_id JOIN source src ON src.id=s.source_id
                WHERE m.site_id<>? GROUP BY 1,2,3,4,5 ORDER BY 1,4""", (SITE_YWM,))))
# ---- SERP
dump_csv("R2-serp-results.csv.gz", ["keyword", "se", "region", "position", "url", "url_domain", "title", "captured_on", "task_id"],
         con.execute("SELECT keyword, se, region, position, url, url_domain, title, captured_on, task_id FROM serp_result ORDER BY captured_on, keyword, se, region, position"))
dump_csv("R2-serp-tasks.csv.gz", ["id", "task_id", "status", "phrases", "stored", "created_at", "finished_at"],
         con.execute("SELECT id, task_id, status, phrases, stored, created_at, finished_at FROM serp_task ORDER BY id"))
# ---- вордстат, проекты, журнал сборов
dump_csv("R2-wordstat-history.csv.gz", ["date", "query", "region", "device", "match_type", "value", "captured_at"],
         con.execute("SELECT date, query, region, device, match_type, value, captured_at FROM wordstat_history ORDER BY query, date"))
dump_csv("R2-wordstat-series.csv.gz", ["date", "query", "region", "device", "granularity", "match_type", "value"],
         con.execute("SELECT date, query, region, device, granularity, match_type, value FROM wordstat_series ORDER BY query, granularity, date"))
dump_csv("R2-projects-urls.csv.gz", ["project_id", "project_name", "site_id", "favorite_goals", "url"],
         con.execute("SELECT p.id, p.name, p.site_id, p.favorite_goals, u.url FROM project_url u JOIN project p ON p.id=u.project_id ORDER BY p.id, u.id"))
dump_csv("R2-collection-run.csv.gz", ["id", "source_id", "site_id", "job_type", "target_date", "status", "rows_written", "error_text", "started_at", "finished_at"],
         con.execute("SELECT id, source_id, site_id, job_type, target_date, status, rows_written, substr(error_text,1,200), started_at, finished_at FROM collection_run ORDER BY id"))
dump_csv("R2-url-brand.csv.gz", ["domain", "url_key", "brand"],
         con.execute("SELECT domain, url_key, brand FROM url_brand ORDER BY domain, url_key"))
con.close()
print("готово за %ds" % (time.time() - T0))
