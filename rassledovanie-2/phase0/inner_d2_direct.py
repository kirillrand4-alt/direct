# -*- coding: utf-8 -*-
"""D2: выгрузка таблиц Директа из панели + конфиг вкладки (наборы целей, разрезы) на дроп, префикс R2-."""
import sqlite3, csv, gzip, io, os, re, json, time, tempfile, urllib.request, collections, base64, hashlib
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


# --- конфиг вкладки Директа (наборы целей, разрезы) — это настройки, не секреты
from cryptography.fernet import Fernet
SK = ""
for ln in open(r"C:\seostat\.env", encoding="utf-8", errors="replace"):
    m = re.match(r"\s*SECRET_KEY\s*=\s*(.+?)\s*$", ln)
    if m:
        SK = m.group(1).split("#")[0].strip()
BOX = Fernet(base64.urlsafe_b64encode(hashlib.sha256(SK.encode()).digest()))
CFG = {}
for k, v in q("SELECT key, value FROM app_setting ORDER BY key"):
    if k.startswith(("direct_goal_sets:", "direct_goals:", "direct_breakdowns:")) or k in (
            "direct_main_domain", "pages_url_filter", "serp_kw_filter", "wordstat_keylist", "wordstat_keylist:meyer", "gsc_oauth_backfill_days"):
        try:
            CFG[k] = BOX.decrypt(str(v).encode()).decode()
        except Exception:
            CFG[k] = str(v)
print("=== конфиг вкладки Директа и панели (расшифровано) ===")
for k, v in CFG.items():
    print("   %-40s %s" % (k, v.replace("\n", " | ")[:300]))
put_bytes("R2-direct-config.json", json.dumps(CFG, ensure_ascii=False, indent=1).encode("utf-8"))
print("", flush=True)

dump_csv("R2-direct-daily.csv.gz", ["domain", "date", "goal_key", "attribution", "impressions", "clicks", "cost", "conversions"],
         con.execute("SELECT domain, date, goal_key, attribution, impressions, clicks, cost, conversions FROM direct_daily ORDER BY domain, date, goal_key"))
dump_csv("R2-direct-campaign-daily.csv.gz", ["domain", "date", "campaign_id", "campaign_name", "goal_key", "attribution", "impressions", "clicks", "cost", "conversions"],
         con.execute("SELECT domain, date, campaign_id, campaign_name, goal_key, attribution, impressions, clicks, cost, conversions FROM direct_campaign_daily ORDER BY domain, date, campaign_id, goal_key"))
dump_csv("R2-direct-breakdown-daily.csv.gz", ["domain", "date", "kind", "key_id", "key_text", "campaign_id", "goal_key", "attribution", "impressions", "clicks", "cost", "conversions"],
         con.execute("SELECT domain, date, kind, key_id, key_text, campaign_id, goal_key, attribution, impressions, clicks, cost, conversions FROM direct_breakdown_daily ORDER BY domain, kind, date"))
dump_csv("R2-direct-collect-run.csv.gz", ["id", "domain", "job_type", "target_date", "status", "rows_written", "error_text", "started_at", "finished_at"],
         con.execute("SELECT id, domain, job_type, target_date, status, rows_written, substr(error_text,1,300), started_at, finished_at FROM direct_collect_run ORDER BY id"))
dump_csv("R2-direct-change.csv.gz", ["id", "domain", "detected_at", "object_type", "object_id", "object_name", "kind", "field", "old_value", "new_value"],
         con.execute("SELECT id, domain, detected_at, object_type, object_id, object_name, kind, field, old_value, new_value FROM direct_change ORDER BY id"))
dump_csv("R2-direct-settings-snapshot.csv.gz", ["domain", "object_type", "object_id", "object_name", "updated_at", "payload"],
         con.execute("SELECT domain, object_type, object_id, object_name, updated_at, payload FROM direct_settings_snapshot ORDER BY domain, object_type, object_id"))
dump_csv("R2-direct-account.csv.gz", ["id", "login", "client_id", "client_info", "campaigns", "live_campaigns", "domain", "domains", "status", "scanned_at", "clicks_90d", "cost_90d"],
         con.execute("SELECT id, login, client_id, client_info, campaigns, live_campaigns, domain, domains, status, scanned_at, clicks_90d, cost_90d FROM direct_account ORDER BY id"))
print("\n=== типы кампаний в снапшоте настроек (object_type=campaign): Type × домен ===")
cnt = collections.Counter()
for dom, payload in con.execute("SELECT domain, payload FROM direct_settings_snapshot WHERE object_type='campaign'"):
    try:
        j = json.loads(payload)
    except Exception:
        cnt[(dom, "?json")] += 1
        continue
    cnt[(dom, str(j.get("Type") or j.get("type") or list(j.keys())[:3]))] += 1
for k, n in sorted(cnt.items()):
    print("   %-26s %-40s %d" % (k[0], k[1][:40], n))
print("\n=== object_type в снапшоте ===", q("SELECT object_type, COUNT(*) FROM direct_settings_snapshot GROUP BY 1"))
con.close()
print("готово за %ds" % (time.time() - T0))
