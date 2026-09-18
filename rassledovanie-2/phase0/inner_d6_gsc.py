# -*- coding: utf-8 -*-
"""D6: GSC API для sc-domain:enger-air.ru: sitemaps, запрос×страница в дополнительных окнах, страна×страница/запрос вокруг
июля 2025, и URL Inspection по ~400 URL (индексация, canonical, дата обхода). Файлы R2-gsc-*."""
import sys, re, json, base64, hashlib, sqlite3, csv, gzip, io, os, time, urllib.request, collections
sys.path.insert(0, r"C:\seostat")
from cryptography.fernet import Fernet
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
DROP_URL = os.environ.get("DROP_URL", "https://parsercompressor.online/drop").rstrip("/")
TOK = os.environ.get("DROP_TOKEN", "")
if not TOK:
    for ln in open(r"C:\sender\server\runner-secrets.env", encoding="utf-8", errors="replace"):
        m = re.match(r"\s*DROP_TOKEN\s*=\s*(\S+)", ln)
        if m:
            TOK = m.group(1)
T0 = time.time()


def put_bytes(name, blob):
    err = None
    for attempt in range(3):
        try:
            r = urllib.request.Request("%s/%s" % (DROP_URL, name), data=blob, method="PUT", headers={"X-Drop-Token": TOK})
            urllib.request.urlopen(r, timeout=600).read()
            return True
        except Exception as e:
            err = e
            time.sleep(5)
    print("   !! не выгружен %s: %s" % (name, str(err)[:100]))
    return False


def dump_rows(name, header, rows):
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\n")
    w.writerow(header)
    for r in rows:
        w.writerow(r)
    blob = gzip.compress(buf.getvalue().encode("utf-8")) if name.endswith(".gz") else buf.getvalue().encode("utf-8")
    ok = put_bytes(name, blob)
    print("   %-52s %9d строк %10d байт %s  [%ds]" % (name, len(rows), len(blob), "OK" if ok else "FAIL", time.time() - T0), flush=True)


SK = ""
for ln in open(r"C:\seostat\.env", encoding="utf-8", errors="replace"):
    m = re.match(r"\s*SECRET_KEY\s*=\s*(.+?)\s*$", ln)
    if m:
        SK = m.group(1).split("#")[0].strip()
BOX = Fernet(base64.urlsafe_b64encode(hashlib.sha256(SK.encode()).digest()))
con = sqlite3.connect("file:C:/seostat/data/seo.db?mode=ro", uri=True)
RAW = dict(con.execute("SELECT key, value FROM app_setting").fetchall())
cr = lambda k: BOX.decrypt(str(RAW[k]).encode()).decode().strip()
creds = Credentials(None, refresh_token=cr("gsc_oauth_refresh_token"), token_uri="https://oauth2.googleapis.com/token",
                    client_id=cr("gsc_oauth_client_id"), client_secret=cr("gsc_oauth_client_secret"),
                    scopes=["https://www.googleapis.com/auth/webmasters.readonly"])
svc = build("webmasters", "v3", credentials=creds, cache_discovery=False)
SITE = "sc-domain:enger-air.ru"
ERR = []

# ---------- 1. sitemaps
print("=== 1. sitemaps ===", flush=True)
try:
    sm = svc.sitemaps().list(siteUrl=SITE).execute()
    put_bytes("R2-gsc-sitemaps.json", json.dumps(sm, ensure_ascii=False, indent=1).encode("utf-8"))
    for s in sm.get("sitemap", []):
        print("   ", s.get("path"), "submitted", s.get("lastSubmitted"), "downloaded", s.get("lastDownloaded"), "errors", s.get("errors"), "warnings", s.get("warnings"),
              [(c.get("type"), c.get("submitted"), c.get("indexed")) for c in s.get("contents", [])])
except Exception as e:
    print("   ошибка:", str(e)[:200])
    ERR.append(("sitemaps", str(e)[:200]))


def sa(body):
    for i in range(4):
        try:
            return svc.searchanalytics().query(siteUrl=SITE, body=body).execute().get("rows", [])
        except Exception as e:
            if i == 3:
                ERR.append(("sa", json.dumps(body)[:120], str(e)[:160]))
                return []
            time.sleep(3 * (i + 1))


def paged(body):
    out = []
    start = 0
    while True:
        b = dict(body)
        b["rowLimit"] = 25000
        b["startRow"] = start
        rows = sa(b)
        out.extend(rows)
        if len(rows) < 25000:
            break
        start += 25000
    return out


RUS = [{"filters": [{"dimension": "country", "operator": "equals", "expression": "rus"}]}]
# ---------- 2. запрос × страница в дополнительных окнах (Россия, web)
print("\n=== 2. запрос × страница, окна ===", flush=True)
ROWS = []
WINDOWS = [("2025-07-08", "2025-07-01", "2025-08-31"), ("2025-09", "2025-09-01", "2025-09-30"), ("2025-12-01", "2025-12-01", "2026-01-31"),
           ("2026-02", "2026-02-01", "2026-02-28"), ("2026-03-04", "2026-03-01", "2026-04-30"), ("2026-05-06", "2026-05-01", "2026-06-30"),
           ("2026-07-08", "2026-07-01", "2026-08-31"), ("2026-09", "2026-09-01", "2026-09-17")]
for label, d1, d2 in WINDOWS:
    rows = paged({"startDate": d1, "endDate": d2, "dimensions": ["query", "page"], "type": "web", "dimensionFilterGroups": RUS})
    for a in rows:
        ROWS.append([label, a["keys"][0], a["keys"][1], a.get("clicks", 0), a.get("impressions", 0), round(a.get("position", 0), 2)])
    print("   %-12s %6d строк  [%ds]" % (label, len(rows), time.time() - T0), flush=True)
dump_rows("R2-gsc-query-page-windows-rus.csv.gz", ["window", "query", "page", "clicks", "impressions", "position"], ROWS)
# то же без фильтра страны для июня 2025 и пика (чтобы увидеть иностранный слой)
ROWS = []
for label, d1, d2 in [("2025-06-world", "2025-06-01", "2025-06-30"), ("2025-10-11-world", "2025-10-01", "2025-11-30"), ("2026-07-08-world", "2026-07-01", "2026-08-31")]:
    rows = paged({"startDate": d1, "endDate": d2, "dimensions": ["query", "page", "country"], "type": "web"})
    for a in rows:
        ROWS.append([label, a["keys"][0], a["keys"][1], a["keys"][2], a.get("clicks", 0), a.get("impressions", 0), round(a.get("position", 0), 2)])
    print("   %-16s %6d строк  [%ds]" % (label, len(rows), time.time() - T0), flush=True)
dump_rows("R2-gsc-query-page-country-windows.csv.gz", ["window", "query", "page", "country", "clicks", "impressions", "position"], ROWS)

# ---------- 3. страна × страница и страна × запрос помесячно 2025-04..2025-09 (второй разлом)
print("\n=== 3. страна × страница/запрос, 2025-04..2025-09 ===", flush=True)
import calendar
RP, RQ = [], []
for mo in ("2025-04", "2025-05", "2025-06", "2025-07", "2025-08", "2025-09"):
    y, m = int(mo[:4]), int(mo[5:])
    d1, d2 = "%s-01" % mo, "%s-%02d" % (mo, calendar.monthrange(y, m)[1])
    for a in paged({"startDate": d1, "endDate": d2, "dimensions": ["country", "page"], "type": "web"}):
        RP.append([mo, a["keys"][0], a["keys"][1], a.get("clicks", 0), a.get("impressions", 0), round(a.get("position", 0), 2)])
    for a in paged({"startDate": d1, "endDate": d2, "dimensions": ["country", "query"], "type": "web"}):
        RQ.append([mo, a["keys"][0], a["keys"][1], a.get("clicks", 0), a.get("impressions", 0), round(a.get("position", 0), 2)])
    print("   %s: страницы %d, запросы %d  [%ds]" % (mo, len(RP), len(RQ), time.time() - T0), flush=True)
dump_rows("R2-gsc-country-page-2025.csv.gz", ["month", "country", "page", "clicks", "impressions", "position"], RP)
dump_rows("R2-gsc-country-query-2025.csv.gz", ["month", "country", "query", "clicks", "impressions", "position"], RQ)

# ---------- 4. по дням: свойство целиком web, все страны и только Россия; и устройство × день (Россия)
print("\n=== 4. по дням ===", flush=True)
RD = []
for a in paged({"startDate": "2025-02-01", "endDate": "2026-09-17", "dimensions": ["date"], "type": "web"}):
    RD.append(["world", a["keys"][0], a.get("clicks", 0), a.get("impressions", 0), round(a.get("position", 0), 2)])
for a in paged({"startDate": "2025-02-01", "endDate": "2026-09-17", "dimensions": ["date"], "type": "web", "dimensionFilterGroups": RUS}):
    RD.append(["rus", a["keys"][0], a.get("clicks", 0), a.get("impressions", 0), round(a.get("position", 0), 2)])
for a in paged({"startDate": "2025-02-01", "endDate": "2026-09-17", "dimensions": ["date", "device"], "type": "web", "dimensionFilterGroups": RUS}):
    RD.append(["rus-" + a["keys"][1], a["keys"][0], a.get("clicks", 0), a.get("impressions", 0), round(a.get("position", 0), 2)])
for st in ("image", "video", "news", "discover", "googleNews"):
    for a in paged({"startDate": "2025-02-01", "endDate": "2026-09-17", "dimensions": ["date"], "type": st}):
        RD.append([st, a["keys"][0], a.get("clicks", 0), a.get("impressions", 0), round(a.get("position", 0), 2)])
dump_rows("R2-gsc-property-day.csv.gz", ["slice", "date", "clicks", "impressions", "position"], RD)
# searchAppearance × месяц (все страны)
RA = []
for mo_y in (2025, 2026):
    for m in range(1, 13):
        mo = "%04d-%02d" % (mo_y, m)
        if not ("2025-02" <= mo <= "2026-09"):
            continue
        d1, d2 = "%s-01" % mo, min("%s-%02d" % (mo, calendar.monthrange(mo_y, m)[1]), "2026-09-17")
        for a in sa({"startDate": d1, "endDate": d2, "dimensions": ["searchAppearance"], "type": "web", "rowLimit": 50}):
            RA.append([mo, a["keys"][0], a.get("clicks", 0), a.get("impressions", 0), round(a.get("position", 0), 2)])
dump_rows("R2-gsc-search-appearance-month.csv", ["month", "appearance", "clicks", "impressions", "position"], RA)

# ---------- 5. URL Inspection
print("\n=== 5. URL Inspection ===", flush=True)
q = lambda s, *a: con.execute(s, a).fetchall()
PEAK = dict(q("SELECT p.url, SUM(m.impressions) FROM page_metric_daily m JOIN page p ON p.id=m.page_id WHERE m.site_id=17 AND m.date BETWEEN '2025-10-01' AND '2025-11-30' GROUP BY 1"))
NOW = dict(q("SELECT p.url, SUM(m.impressions) FROM page_metric_daily m JOIN page p ON p.id=m.page_id WHERE m.site_id=17 AND m.date BETWEEN '2026-07-01' AND '2026-09-17' GROUP BY 1"))
PEAKC = dict(q("SELECT p.url, SUM(m.clicks) FROM page_metric_daily m JOIN page p ON p.id=m.page_id WHERE m.site_id=17 AND m.date BETWEEN '2025-10-01' AND '2025-11-30' GROUP BY 1"))
NOWC = dict(q("SELECT p.url, SUM(m.clicks) FROM page_metric_daily m JOIN page p ON p.id=m.page_id WHERE m.site_id=17 AND m.date BETWEEN '2026-07-01' AND '2026-09-17' GROUP BY 1"))
con.close()
urls = set(PEAK) | set(NOW)
score = {u: (PEAK.get(u, 0) + NOW.get(u, 0)) + 50 * (PEAKC.get(u, 0) + NOWC.get(u, 0)) for u in urls}
cand = sorted(urls, key=lambda u: -score[u])
lost = [u for u in urls if PEAKC.get(u, 0) >= 2 and NOW.get(u, 0) == 0]
sel = []
for u in lost + cand:
    if u not in sel and u.startswith("http"):
        sel.append(u)
    if len(sel) >= 420:
        break
print("   кандидатов %d, потерянных полностью %d, инспектируем %d" % (len(urls), len(lost), len(sel)), flush=True)
try:
    insp = build("searchconsole", "v1", credentials=creds, cache_discovery=False)
except Exception as e:
    insp = None
    print("   build searchconsole: %s" % str(e)[:200])
OUT = []
CSV = []
t1 = time.time()
for i, u in enumerate(sel):
    if not insp:
        break
    if time.time() - T0 > 1500:
        print("   стоп по времени на %d" % i)
        break
    rec = {"url": u, "peak_impr": PEAK.get(u, 0), "now_impr": NOW.get(u, 0), "peak_clicks": PEAKC.get(u, 0), "now_clicks": NOWC.get(u, 0)}
    try:
        r = insp.urlInspection().index().inspect(body={"inspectionUrl": u, "siteUrl": SITE, "languageCode": "ru-RU"}).execute()
        rec["result"] = r.get("inspectionResult", {})
    except Exception as e:
        rec["error"] = str(e)[:300]
        if "429" in str(e) or "quota" in str(e).lower():
            print("   квота/429 на %d: %s" % (i, str(e)[:120]))
            time.sleep(20)
    OUT.append(rec)
    ir = rec.get("result", {}).get("indexStatusResult", {})
    CSV.append([u, rec["peak_impr"], rec["now_impr"], rec["peak_clicks"], rec["now_clicks"], ir.get("verdict", ""), ir.get("coverageState", ""),
                ir.get("robotsTxtState", ""), ir.get("indexingState", ""), ir.get("lastCrawlTime", ""), ir.get("pageFetchState", ""),
                ir.get("googleCanonical", ""), ir.get("userCanonical", ""), ir.get("crawledAs", ""), "|".join(ir.get("sitemap", []) or []),
                len(ir.get("referringUrls", []) or []), rec.get("result", {}).get("mobileUsabilityResult", {}).get("verdict", ""),
                rec.get("result", {}).get("richResultsResult", {}).get("verdict", ""), rec.get("error", "")[:120]])
    if (i + 1) % 50 == 0:
        print("   %d/%d  [%ds]" % (i + 1, len(sel), time.time() - T0), flush=True)
    time.sleep(0.25)
put_bytes("R2-gsc-inspect-enger.jsonl.gz", gzip.compress("\n".join(json.dumps(x, ensure_ascii=False) for x in OUT).encode("utf-8")))
dump_rows("R2-gsc-inspect-enger.csv", ["url", "peak_impr", "now_impr", "peak_clicks", "now_clicks", "verdict", "coverage_state", "robots_state", "indexing_state",
                                       "last_crawl", "fetch_state", "google_canonical", "user_canonical", "crawled_as", "sitemaps", "referring_urls_n", "mobile_verdict", "rich_verdict", "error"], CSV)
cs = collections.Counter((r[5], r[6]) for r in CSV)
print("\n   вердикт × coverageState:")
for k, n in cs.most_common():
    print("     %5d  %s | %s" % (n, k[0], k[1]))
print("\n=== ОШИБКИ (%d) ===" % len(ERR))
for e in ERR[:40]:
    print("   ", e)
print("готово за %ds" % (time.time() - T0))
