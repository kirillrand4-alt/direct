# -*- coding: utf-8 -*-
"""S2: живая выдача Google с сервера по списку запросов, десктоп + мобильный. Запускать ТОЛЬКО если S1 прошёл
без страницы проверки трафика. Штатный Chromium панели, без флагов маскировки, пауза 10 с между запросами
(34 запроса × 2 устройства ≈ 15 минут). При первой странице проверки трафика — остановка.
На дроп: R2-serp-<qNN>-<desktop|mobile>.html.gz и .png, R2-serp-full-result.json (разбор: позиции строк в пикселях,
блоки, признаки обзора ИИ / галереи / рекламы)."""
import os, sys, re, json, time, gzip, urllib.request, urllib.parse
DROP_URL = os.environ.get("DROP_URL", "https://parsercompressor.online/drop").rstrip("/")
TOK = os.environ.get("DROP_TOKEN", "")
if not TOK:
    for ln in open(r"C:\sender\server\runner-secrets.env", encoding="utf-8", errors="replace"):
        m = re.match(r"\s*DROP_TOKEN\s*=\s*(\S+)", ln)
        if m:
            TOK = m.group(1)


def put_bytes(name, blob):
    r = urllib.request.Request("%s/%s" % (DROP_URL, name), data=blob, method="PUT", headers={"X-Drop-Token": TOK})
    urllib.request.urlopen(r, timeout=300).read()


def find_chromium():
    roots = [os.environ.get("PLAYWRIGHT_BROWSERS_PATH", ""), r"C:\Users\Administrator\AppData\Local\ms-playwright",
             os.path.expanduser(r"~\AppData\Local\ms-playwright"),
             r"C:\Windows\system32\config\systemprofile\AppData\Local\ms-playwright", r"C:\Windows\SysWOW64\config\systemprofile\AppData\Local\ms-playwright"]
    for root in roots:
        if root and os.path.isdir(root):
            for d in sorted(os.listdir(root), reverse=True):
                for sub in ("chrome-win", "chrome-win64", "chrome-headless-shell-win64", ""):
                    for exe in ("chrome.exe", "headless_shell.exe"):
                        p = os.path.join(root, d, sub, exe) if sub else os.path.join(root, d, exe)
                        if os.path.exists(p):
                            return p
    return None


JS = r"""
() => {
  const sy = window.scrollY;
  const box = e => { const r = e.getBoundingClientRect(); return {top: Math.round(r.top + sy), h: Math.round(r.height), left: Math.round(r.left), w: Math.round(r.width)}; };
  const seen = new Set(); const organic = [];
  document.querySelectorAll('#search a h3, #rso a h3, #main a h3, a[role="link"] div[role="heading"]').forEach(h3 => {
    const a = h3.closest('a'); if (!a) return; const href = a.href || ''; if (!href || seen.has(href)) return; seen.add(href);
    const b = box(h3); organic.push({href, title: (h3.innerText || '').slice(0, 120), top: b.top, left: b.left});
  });
  const ads = [];
  document.querySelectorAll('#tads, #tadsb, #bottomads, #tvcap, [data-text-ad], [aria-label="Реклама"], [aria-label="Ads"]').forEach(e => {
    const b = box(e); ads.push({sel: (e.id || e.getAttribute('aria-label') || e.className || '').toString().slice(0, 40), top: b.top, h: b.h, links: e.querySelectorAll('a[href]').length, text: (e.innerText || '').replace(/\s+/g, ' ').slice(0, 300)});
  });
  const blocks = [];
  document.querySelectorAll('#rso > div, #search > div > div > div, #center_col > div, #main > div').forEach(e => {
    const b = box(e); if (b.h < 20) return;
    blocks.push({top: b.top, h: b.h, cls: (e.className || '').toString().slice(0, 50), text: (e.innerText || '').replace(/\s+/g, ' ').slice(0, 140)});
  });
  const body = document.body.innerText || '';
  const marks = {};
  for (const [k, re] of Object.entries({aio: /Обзор от ИИ|AI Overview|Обзор ИИ|Создано с помощью ИИ|Generative AI/i, sponsored: /Реклама|Спонсируемые|Sponsored/i, shopping: /Товары|Купить в интернете|Shopping|Цена от|Магазины/i, paa: /Похожие вопросы|People also ask|Вопросы по теме/i, maps: /Карты|Maps|Адрес|Часы работы/i, video: /Видео/i, unusual: /необычн|unusual traffic|подозрительн/i})) marks[k] = re.test(body);
  return {organic, ads, blocks, marks, docH: document.documentElement.scrollHeight, vh: window.innerHeight, title: document.title, url: location.href, bodyLen: body.length};
}
"""

QUERIES = ["винтовой компрессор", "адсорбционный осушитель", "точка росы это", "enger",
           "винтовой компрессор цена", "винтовой компрессор купить цена", "купить компрессор винтовой воздушный",
           "компрессор дизельный передвижной", "азотные станции", "азотная установка купить",
           "адсорбционный осушитель воздуха", "генератор азота купить", "компрессор с осушителем",
           "адсорбционный осушитель горячей регенерации", "винтовые компрессоры", "центробежный компрессор купить",
           "установка для производства азота", "компрессор винтовой купить", "компрессор винтовой воздушный цена",
           "безмасляный компрессор", "рефрижераторный осушитель", "кислородная установка", "кислородный генератор купить",
           "роторная воздуходувка", "дизельный компрессор купить", "компрессорная станция", "винтовой компрессор 15 квт",
           "винтовой компрессор 22 квт", "винтовой компрессор 37 квт", "осушитель сжатого воздуха",
           "модульная компрессорная станция", "промышленный компрессор купить", "enger компрессор", "энгер компрессор"]
out = {"chromium": find_chromium(), "started": time.strftime("%Y-%m-%d %H:%M:%S"), "results": []}
print("chromium:", out["chromium"], "запросов:", len(QUERIES), flush=True)
from playwright.sync_api import sync_playwright
stop = False
with sync_playwright() as p:
    kw = {"headless": True}
    if out["chromium"]:
        kw["executable_path"] = out["chromium"]
    browser = p.chromium.launch(**kw)
    for device in ("desktop", "mobile"):
        if stop:
            break
        if device == "desktop":
            ctx = browser.new_context(locale="ru-RU", timezone_id="Europe/Moscow", viewport={"width": 1366, "height": 900})
        else:
            dev = p.devices.get("iPhone 13") or p.devices.get("Pixel 5") or {}
            ctx = browser.new_context(**dev, locale="ru-RU", timezone_id="Europe/Moscow")
        page = ctx.new_page()
        try:
            page.goto("https://www.google.com/?hl=ru", timeout=45000, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)
        except Exception as e:
            out["home_err_%s" % device] = str(e)[:200]
        for i, qtext in enumerate(QUERIES):
            rec = {"q": qtext, "device": device, "n": i + 1}
            slug = "q%02d-%s" % (i + 1, device)
            try:
                url = "https://www.google.com/search?q=%s&hl=ru&gl=ru&pws=0" % urllib.parse.quote(qtext)
                resp = page.goto(url, timeout=45000, wait_until="domcontentloaded")
                rec["status"] = resp.status if resp else None
                page.wait_for_timeout(3500)
                rec["final_url"] = page.url
                html = page.content()
                low = html.lower()
                rec["blocked"] = ("/sorry/" in page.url) or ("unusual traffic" in low) or ("необычный трафик" in low)
                if rec["blocked"]:
                    out["results"].append(rec)
                    print("СТОП на %s/%s: страница проверки трафика" % (slug, qtext), flush=True)
                    stop = True
                    break
                try:
                    rec["parse"] = page.evaluate(JS)
                except Exception as e:
                    rec["parse_err"] = str(e)[:200]
                put_bytes("R2-serp-%s.html.gz" % slug, gzip.compress(html.encode("utf-8")))
                png = page.screenshot(full_page=True)
                put_bytes("R2-serp-%s.png" % slug, png)
                rec["files"] = ["R2-serp-%s.html.gz" % slug, "R2-serp-%s.png" % slug]
                pz = rec.get("parse") or {}
                eng = [o for o in pz.get("organic", []) if "enger-air" in o.get("href", "")]
                rec["enger_top_px"] = eng[0]["top"] if eng else None
                rec["enger_rank"] = ([k for k, o in enumerate(pz.get("organic", []), 1) if "enger-air" in o.get("href", "")] or [None])[0]
                print("   %-14s enger: ранг %s, высота %s px | блоков %d, реклама %d, метки %s" % (
                    slug, rec["enger_rank"], rec["enger_top_px"], len(pz.get("blocks", [])), len(pz.get("ads", [])), pz.get("marks")), flush=True)
            except Exception as e:
                rec["error"] = str(e)[:300]
                print("   %-14s ошибка %s" % (slug, rec["error"][:100]), flush=True)
            out["results"].append(rec)
            time.sleep(10)
        ctx.close()
    browser.close()
put_bytes("R2-serp-full-result.json", json.dumps(out, ensure_ascii=False, indent=1).encode("utf-8"))
print("готово; результат R2-serp-full-result.json; записей %d" % len(out["results"]))
