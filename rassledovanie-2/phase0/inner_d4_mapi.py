# -*- coding: utf-8 -*-
"""D4: API Метрики по всем счётчикам холдинга: справочник целей по названиям, помесячно источник×поисковик×цели,
кампании Директа × цели по атрибуции Метрики (lastsign), сравнение моделей атрибуции. Файлы R2-metrika-api-*."""
import sqlite3, base64, hashlib, json, re, time, csv, gzip, io, os, tempfile, urllib.request, urllib.parse, calendar
from cryptography.fernet import Fernet
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


env = {}
for ln in open(r"C:\seostat\.env", encoding="utf-8", errors="ignore"):
    if "=" in ln and not ln.strip().startswith("#"):
        k, v = ln.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
BOX = Fernet(base64.urlsafe_b64encode(hashlib.sha256(env.get("SECRET_KEY", "").encode()).digest()))
con = sqlite3.connect("file:C:/seostat/data/seo.db?mode=ro", uri=True)
MT = None
for k, v in con.execute("SELECT key, value FROM app_setting"):
    if k == "yandex_direct_token":
        try:
            MT = BOX.decrypt(v.encode()).decode()
        except Exception:
            MT = v
con.close()
H = {"Authorization": "OAuth " + MT}

COUNTERS = {46635618: "prokompressor.ru", 91234386: "enger-air.ru", 51445799: "remeza-kompressor.ru", 86085222: "berg-compressor.com",
            52376263: "berg-kompressor.ru", 95621358: "crossair-compressor.ru", 53282902: "comaro-kompressor.ru", 67311745: "ac-kompressor.ru",
            50642803: "abac-kompressor.ru", 64505332: "dali-kompressor.ru", 55649995: "cmprg.ru", 96820870: "ironmac-compressor.com",
            79279948: "zif-kompressor.ru", 50710651: "ekomak-kompressor.com", 88600658: "fini-compressor.com", 94657480: "oil-free.ru",
            103337642: "dizel-compressor.ru", 54870637: "kraftmann-kompressor.com", 86084043: "ekomak-compressor.com",
            62694676: "meyer-corp.ru", 55078348: "usort.ru", 98766343: "vsefotoseparatory.ru"}
ERR = []


def get(url, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=H)
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:300]
            if e.code in (429, 503, 500) and i < tries - 1:
                time.sleep(3 * (i + 1))
                continue
            raise RuntimeError("HTTP %s %s" % (e.code, body))
        except Exception as e:
            if i < tries - 1:
                time.sleep(2)
                continue
            raise


def stat(**p):
    p.setdefault("accuracy", "full")
    p.setdefault("limit", 1000)
    return get("https://api-metrika.yandex.net/stat/v1/data?" + urllib.parse.urlencode(p))


# ---------- 1. цели по названиям
print("=== 1. цели счётчиков ===", flush=True)
GOALS = []
PICK = {}
KEYS = [("qual", ("квалифицированный лид", "квалифиц")), ("all", ("все лиды",)), ("call", ("коллтрекинг",)), ("mail", ("емейлтрекинг", "емейл", "email")),
        ("deal", ("сделка успешна",)), ("spam", ("спам",)), ("b24q", ("[b242ya] лид квалифи",)), ("b24n", ("[b242ya] новый лид",)),
        ("form", ("отправка формы", "отправил форму", "форма")), ("auto_contacts", ("автоцель: заполнил контактные",)), ("thanks", ("спасибо",))]
for cid, dom in COUNTERS.items():
    try:
        g = get("https://api-metrika.yandex.net/management/v1/counter/%d/goals?useDeleted=true" % cid).get("goals", [])
    except Exception as e:
        ERR.append(("goals", cid, str(e)[:120]))
        continue
    pk = {}
    for x in g:
        name = x.get("name", "")
        GOALS.append([cid, dom, x.get("id"), name, x.get("type"), x.get("is_retargeting"), x.get("default_price"), x.get("is_favorite"), (x.get("conditions") or [{}])[0].get("url", "") if x.get("conditions") else ""])
        nl = name.lower()
        for key, pats in KEYS:
            if key in pk:
                continue
            if any(p in nl for p in pats):
                # roistat-цели предпочтительнее одноимённых
                if key in ("qual", "all", "call", "mail", "deal", "spam") and "roistat" not in nl:
                    continue
                pk[key] = str(x.get("id"))
    PICK[cid] = pk
    print("   %-26s целей %3d  %s" % (dom, len(g), pk), flush=True)
    time.sleep(0.2)
dump_rows("R2-metrika-goals-all.csv", ["counter_id", "domain", "goal_id", "name", "type", "is_retargeting", "default_price", "is_favorite", "cond_url"], GOALS)
put_bytes("R2-metrika-goal-pick.json", json.dumps({str(k): v for k, v in PICK.items()}, ensure_ascii=False, indent=1).encode("utf-8"))

MONTHS = []
for y in (2025, 2026):
    for m in range(1, 13):
        d1 = "%04d-%02d-01" % (y, m)
        if "2025-06" <= d1[:7] <= "2026-09":
            d2 = "%04d-%02d-%02d" % (y, m, calendar.monthrange(y, m)[1])
            MONTHS.append((d1[:7], d1, min(d2, "2026-09-17")))


def metrics_for(cid):
    pk = PICK.get(cid, {})
    ms = ["ym:s:visits", "ym:s:users", "ym:s:bounceRate", "ym:s:pageDepth", "ym:s:avgVisitDurationSeconds"]
    names = ["visits", "users", "bounce_rate", "page_depth", "avg_duration"]
    for key in ("qual", "all", "call", "mail", "deal", "spam", "b24q", "b24n", "form"):
        if key in pk:
            ms.append("ym:s:goal%sreaches" % pk[key])
            names.append("g_" + key)
    return ms, names


# ---------- 2. помесячно: lastsign источник × поисковик × цели
print("\n=== 2. источник × поисковик × месяц (lastsign) ===", flush=True)
ROWS = []
for cid, dom in COUNTERS.items():
    ms, names = metrics_for(cid)
    for mo, d1, d2 in MONTHS:
        try:
            d = stat(ids=cid, metrics=",".join(ms), dimensions="ym:s:lastsignTrafficSource,ym:s:lastsignSearchEngineRoot,ym:s:lastsignAdvEngine",
                     date1=d1, date2=d2, attribution="lastsign")
        except Exception as e:
            ERR.append(("src", cid, mo, str(e)[:160]))
            continue
        for it in d.get("data", []):
            dims = [(x.get("name") or "") for x in it["dimensions"]]
            ROWS.append([cid, dom, mo] + dims + it["metrics"])
        time.sleep(0.15)
    print("   %-26s строк %d  [%ds]" % (dom, len(ROWS), time.time() - T0), flush=True)
HDR2 = ["counter_id", "domain", "month", "traffic_source", "search_engine", "adv_engine"]
# метрики различаются по счётчикам — приводим к общему набору колонок
ALLN = ["visits", "users", "bounce_rate", "page_depth", "avg_duration", "g_qual", "g_all", "g_call", "g_mail", "g_deal", "g_spam", "g_b24q", "g_b24n", "g_form"]
OUT2 = []
for r in ROWS:
    cid = r[0]
    ms, names = metrics_for(cid)
    vals = dict(zip(names, r[6:]))
    OUT2.append(r[:6] + [vals.get(n, "") for n in ALLN])
dump_rows("R2-metrika-api-source-month.csv.gz", HDR2 + ALLN, OUT2)

# ---------- 3. кампании Директа × цели (атрибуция Метрики lastsign), помесячно
print("\n=== 3. кампании Директа × цели (lastsign) ===", flush=True)
ROWS3 = []
for cid, dom in COUNTERS.items():
    ms, names = metrics_for(cid)
    for mo, d1, d2 in MONTHS:
        if mo < "2025-06":
            continue
        try:
            d = stat(ids=cid, metrics=",".join(ms), dimensions="ym:s:lastsignDirectClickOrder,ym:s:lastsignDirectPlatformType",
                     date1=d1, date2=d2, attribution="lastsign", filters="ym:s:lastsignTrafficSource=='ad'")
        except Exception as e:
            ERR.append(("direct", cid, mo, str(e)[:160]))
            continue
        for it in d.get("data", []):
            o = it["dimensions"][0]
            pt = it["dimensions"][1]
            vals = dict(zip(names, it["metrics"]))
            ROWS3.append([cid, dom, mo, o.get("id") or "", (o.get("name") or "")[:150], (pt.get("name") or "")] + [vals.get(n, "") for n in ALLN])
        time.sleep(0.15)
    print("   %-26s строк %d  [%ds]" % (dom, len(ROWS3), time.time() - T0), flush=True)
dump_rows("R2-metrika-api-direct-campaign-month.csv.gz", ["counter_id", "domain", "month", "campaign_id", "campaign_name", "platform_type"] + ALLN, ROWS3)

# ---------- 4. сравнение моделей атрибуции по источникам (июн–авг 2026), 8 крупнейших счётчиков
print("\n=== 4. модели атрибуции ===", flush=True)
MODELS = [("first", "first"), ("last", "last"), ("lastsign", "lastsign"), ("lastDirectClick", "last_yandex_direct_click"),
          ("crossDeviceFirst", "cross_device_first"), ("crossDeviceLast", "cross_device_last"), ("crossDeviceLastsign", "cross_device_last_significant"),
          ("automatic", "automatic"), ("auto", "automatic")]
ROWS4 = []
for cid in (46635618, 91234386, 51445799, 86085222, 52376263, 64505332, 95621358, 67311745):
    dom = COUNTERS[cid]
    ms, names = metrics_for(cid)
    for prefix, attr in MODELS:
        try:
            d = stat(ids=cid, metrics=",".join(ms), dimensions="ym:s:%sTrafficSource,ym:s:%sSearchEngineRoot" % (prefix, prefix),
                     date1="2026-06-01", date2="2026-08-31", attribution=attr)
        except Exception as e:
            ERR.append(("attr", cid, prefix, str(e)[:120]))
            print("   %-22s %-20s ошибка: %s" % (dom, prefix, str(e)[:80]))
            continue
        for it in d.get("data", []):
            vals = dict(zip(names, it["metrics"]))
            ROWS4.append([cid, dom, prefix, attr, (it["dimensions"][0].get("name") or ""), (it["dimensions"][1].get("name") or "")] + [vals.get(n, "") for n in ALLN])
        time.sleep(0.2)
dump_rows("R2-metrika-api-attribution-models.csv", ["counter_id", "domain", "dim_prefix", "attribution", "traffic_source", "search_engine"] + ALLN, ROWS4)

# ---------- 5. Директ: поисковые фразы × цели (lastsign) для enger и prokompressor, 2026-03..09 целиком
print("\n=== 5. фразы Директа × цели ===", flush=True)
ROWS5 = []
for cid in (91234386, 46635618):
    dom = COUNTERS[cid]
    ms, names = metrics_for(cid)
    for mo, d1, d2 in MONTHS:
        if mo < "2026-03":
            continue
        offset = 1
        while True:
            try:
                d = stat(ids=cid, metrics=",".join(ms), dimensions="ym:s:lastsignDirectClickOrder,ym:s:lastsignDirectSearchPhrase",
                         date1=d1, date2=d2, attribution="lastsign", filters="ym:s:lastsignTrafficSource=='ad'", limit=10000, offset=offset)
            except Exception as e:
                ERR.append(("phrase", cid, mo, str(e)[:160]))
                break
            data = d.get("data", [])
            for it in data:
                vals = dict(zip(names, it["metrics"]))
                ROWS5.append([cid, dom, mo, it["dimensions"][0].get("id") or "", (it["dimensions"][1].get("name") or "")[:200]] + [vals.get(n, "") for n in ALLN])
            if len(data) < 10000:
                break
            offset += 10000
            time.sleep(0.3)
        time.sleep(0.2)
    print("   %-26s строк %d  [%ds]" % (dom, len(ROWS5), time.time() - T0), flush=True)
dump_rows("R2-metrika-api-direct-phrase-month.csv.gz", ["counter_id", "domain", "month", "campaign_id", "search_phrase"] + ALLN, ROWS5)

print("\n=== ОШИБКИ (%d) ===" % len(ERR))
for e in ERR[:60]:
    print("   ", e)
put_bytes("R2-metrika-api-errors.json", json.dumps(ERR, ensure_ascii=False, indent=1).encode("utf-8"))
print("готово за %ds" % (time.time() - T0))
