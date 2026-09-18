# -*- coding: utf-8 -*-
"""D3: Метрика всего холдинга из таблицы visit (+hit для рефереров): срезы на дроп, префикс R2-."""
import sqlite3, csv, gzip, io, os, re, json, time, tempfile, urllib.request, collections
from urllib.parse import urlsplit
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
DIG = re.compile(r"\d+")
T0 = time.time()


def goals_of(ex):
    if not ex or "goalsID" not in ex:
        return ""
    try:
        return ",".join(sorted(set(DIG.findall(str(json.loads(ex).get("goalsID") or "")))))
    except Exception:
        return ""


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


ROI = re.compile(r"roistat=direct(\d+)_(search|context|[a-z]+)_(\d+)")
UTMC = re.compile(r"utm_campaign=([^&]*)")
AI = re.compile(r"(?i)perplexity|alice\.yandex|ya\.ru/alice|openai|chatgpt|chat\.openai|gemini\.google|bard\.google|claude\.ai|anthropic|deepseek|gigachat|copilot|bing\.com/chat|you\.com|phind|mistral|neuro|нейро|yandex\.ru/search/.*neuro|dzen\.ru/a|character\.ai|poe\.com|grok|x\.ai|kagi|andisearch|iask|sber\.ru/gigachat|giga\.chat")
COLS = "counter_id,date,date_time,client_id,visit_id,traffic_source,search_engine,adv_engine,referer,start_url,end_url,page_views,duration,bounce,device,os,browser,region_city,ip,extra"
HDR = COLS.replace("extra", "goals").split(",")


def camp_of(url):
    m = ROI.search(url or "")
    if m:
        return m.group(3), m.group(2), m.group(1)
    m = UTMC.search(url or "")
    if m:
        d = DIG.findall(m.group(1))
        return (d[-1] if d else m.group(1)[:40]), "utm", ""
    return "", "", ""


# --- 1. помесячно: счётчик × источник × поисковик × устройство
print("=== 1. помесячная сводка ===", flush=True)
dump_csv("R2-holding-visits-month.csv.gz", ["counter_id", "month", "traffic_source", "search_engine", "adv_engine", "device", "visits", "visits_with_goals", "bounces", "page_views", "duration"],
         con.execute("""SELECT counter_id, substr(date,1,7), traffic_source, search_engine, adv_engine, device, COUNT(*),
                               SUM(CASE WHEN extra LIKE '%goalsID%' AND extra NOT LIKE '%"goalsID": []%' AND extra NOT LIKE '%"goalsID":[]%' THEN 1 ELSE 0 END),
                               SUM(bounce), SUM(page_views), SUM(duration)
                        FROM visit GROUP BY 1,2,3,4,5,6 ORDER BY 1,2"""))

# --- 2. все визиты НЕ из рекламы (органика, прямые, ссылки, внутренние...) — построчно
print("=== 2. визиты не из рекламы ===", flush=True)
def gen_nonad():
    for r in con.execute("SELECT %s FROM visit WHERE traffic_source IS NULL OR traffic_source<>'ad' ORDER BY date_time" % COLS):
        yield list(r[:-1]) + [goals_of(r[-1])]
dump_csv("R2-holding-visits-nonad.csv.gz", HDR, gen_nonad())

# --- 3. все визиты с хотя бы одной целью (включая рекламу)
print("=== 3. визиты с целями ===", flush=True)
def gen_goals():
    for r in con.execute("SELECT %s FROM visit WHERE extra LIKE '%%goalsID%%' ORDER BY date_time" % COLS):
        g = goals_of(r[-1])
        if g:
            yield list(r[:-1]) + [g]
dump_csv("R2-holding-visits-goals.csv.gz", HDR, gen_goals())

# --- 4. рекламные визиты: агрегат по кампании (из метки roistat) и длинная таблица целей
print("=== 4. рекламные визиты по кампаниям ===", flush=True)
AGG = collections.Counter(); AGG_B = collections.Counter(); AGG_PV = collections.Counter(); AGG_D = collections.Counter(); AGG_CL = collections.defaultdict(set)
GL = collections.Counter()
for cid, d, cl, src, se, adv, su, pv, du, bo, dev, ex in con.execute(
        "SELECT counter_id, date, client_id, traffic_source, search_engine, adv_engine, start_url, page_views, duration, bounce, device, extra FROM visit WHERE traffic_source='ad'"):
    camp, typ, n = camp_of(su)
    k = (cid, str(d)[:7], camp, typ, n, adv or "", dev or "")
    AGG[k] += 1; AGG_B[k] += int(bo or 0); AGG_PV[k] += int(pv or 0); AGG_D[k] += int(du or 0)
    AGG_CL[k].add(cl)
    g = goals_of(ex)
    if g:
        for gid in g.split(","):
            GL[(cid, str(d)[:7], camp, typ, gid)] += 1
dump_csv("R2-holding-ad-campaign-month.csv.gz", ["counter_id", "month", "campaign_id", "src_type", "roistat_n", "adv_engine", "device", "visits", "clients", "bounces", "page_views", "duration"],
         ((k[0], k[1], k[2], k[3], k[4], k[5], k[6], AGG[k], len(AGG_CL[k]), AGG_B[k], AGG_PV[k], AGG_D[k]) for k in sorted(AGG)))
dump_csv("R2-holding-ad-campaign-goal-month.csv.gz", ["counter_id", "month", "campaign_id", "src_type", "goal_id", "visits_with_goal"],
         ((k[0], k[1], k[2], k[3], k[4], GL[k]) for k in sorted(GL)))
del AGG, AGG_B, AGG_PV, AGG_D, AGG_CL, GL

# --- 5. рекламные визиты enger и prokompressor построчно (для перепривязки и калибровки)
print("=== 5. рекламные визиты enger + prokompressor построчно (slim) ===", flush=True)
def gen_ad_slim():
    for r in con.execute("SELECT counter_id, date_time, client_id, visit_id, start_url, page_views, duration, bounce, device, region_city, extra FROM visit WHERE traffic_source='ad' AND counter_id IN (91234386, 46635618) ORDER BY date_time"):
        camp, typ, n = camp_of(r[4])
        path = (r[4] or "").split("?")[0][:200]
        yield [r[0], r[1], r[2], r[3], camp, typ, path, r[5], r[6], r[7], r[8], r[9], goals_of(r[10])]
dump_csv("R2-ad-visits-slim-enger-pk.csv.gz", ["counter_id", "date_time", "client_id", "visit_id", "campaign_id", "src_type", "start_path", "page_views", "duration", "bounce", "device", "region_city", "goals"], gen_ad_slim())

# --- 6. ИИ-ассистенты как источник: визиты с реферером ИИ (все счётчики, все источники)
print("=== 6. ИИ-реферреры ===", flush=True)
def gen_ai():
    for r in con.execute("SELECT %s FROM visit WHERE referer IS NOT NULL AND referer<>'' ORDER BY date_time" % COLS):
        if AI.search(r[8] or ""):
            yield list(r[:-1]) + [goals_of(r[-1])]
dump_csv("R2-holding-ai-referrer-visits.csv.gz", HDR, gen_ai())
# рефереры по хостам помесячно (все визиты с реферером, кроме собственного домена)
print("=== 7. рефереры по хостам ===", flush=True)
RH = collections.Counter(); RG = collections.Counter()
for cid, d, ref, src, ex in con.execute("SELECT counter_id, date, referer, traffic_source, extra FROM visit WHERE referer IS NOT NULL AND referer<>''"):
    try:
        h = urlsplit(ref).netloc.lower()
    except Exception:
        h = "?"
    k = (cid, str(d)[:7], src or "", h[:120])
    RH[k] += 1
    if goals_of(ex):
        RG[k] += 1
dump_csv("R2-holding-referrer-host-month.csv.gz", ["counter_id", "month", "traffic_source", "referer_host", "visits", "visits_with_goals"],
         ((k[0], k[1], k[2], k[3], RH[k], RG[k]) for k in sorted(RH)))
del RH, RG

# --- 8. enger: полный дамп визитов до 2026-09-17 (обновление сентября)
print("=== 8. enger полный дамп ===", flush=True)
def gen_enger():
    for r in con.execute("SELECT %s FROM visit WHERE counter_id=91234386 ORDER BY date_time" % COLS):
        yield list(r[:-1]) + [goals_of(r[-1])]
dump_csv("R2-enger-visits-full.csv.gz", HDR, gen_enger())

# --- 9. hit: utm-метки рекламных хитов помесячно (utm_content = объявление|кампания|устройство)
print("=== 9. hit: utm по рекламе ===", flush=True)
try:
    dump_csv("R2-hit-utm-month.csv.gz", ["counter_id", "month", "traffic_source", "utm_source", "utm_medium", "utm_campaign", "utm_content", "hits", "clients"],
             con.execute("""SELECT counter_id, substr(date,1,7), traffic_source, utm_source, utm_medium, utm_campaign, utm_content, COUNT(*), COUNT(DISTINCT client_id)
                            FROM hit WHERE utm_source IS NOT NULL AND utm_source<>'' AND utm_source<>'None' AND is_page_view=1
                            GROUP BY 1,2,3,4,5,6,7 ORDER BY 1,2"""))
except Exception as e:
    print("   hit utm: ошибка %s" % str(e)[:120])
con.close()
print("готово за %ds" % (time.time() - T0))
