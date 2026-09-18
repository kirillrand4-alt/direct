# -*- coding: utf-8 -*-
"""S1: проба живой российской выдачи Google с сервера (Playwright/Chromium панели, без прокси, штатный браузер,
без каких-либо флагов маскировки). Три запроса, десктоп, пауза 8 с между запросами.
Результат: JSON-разбор (позиции строк в пикселях, блоки рекламы, признаки AI-обзора), HTML.gz и скриншот на дроп.
Если Google показывает страницу «необычный трафик» — остановиться и не повторять; тогда использовать
поставщика выдачи панели (см. 00-ZAPUSK.md, раздел «Живая выдача»)."""
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
  document.querySelectorAll('#search a h3, #rso a h3, #main a h3').forEach(h3 => {
    const a = h3.closest('a'); if (!a) return; const href = a.href || ''; if (!href || seen.has(href)) return; seen.add(href);
    const b = box(h3); organic.push({href, title: (h3.innerText || '').slice(0, 120), top: b.top, left: b.left});
  });
  const ads = [];
  document.querySelectorAll('#tads, #tadsb, #bottomads, #tvcap, [data-text-ad], [aria-label="Реклама"], [aria-label="Ads"]').forEach(e => {
    const b = box(e); ads.push({sel: (e.id || e.getAttribute('aria-label') || e.className || '').toString().slice(0, 40), top: b.top, h: b.h, links: e.querySelectorAll('a[href]').length, text: (e.innerText || '').replace(/\s+/g, ' ').slice(0, 400)});
  });
  const blocks = [];
  document.querySelectorAll('#rso > div, #search > div > div > div, #center_col > div').forEach(e => {
    const b = box(e); if (b.h < 20) return;
    blocks.push({top: b.top, h: b.h, cls: (e.className || '').toString().slice(0, 50), text: (e.innerText || '').replace(/\s+/g, ' ').slice(0, 140)});
  });
  const body = document.body.innerText || '';
  const marks = {};
  for (const [k, re] of Object.entries({aio: /Обзор от ИИ|AI Overview|Обзор ИИ|Создано с помощью ИИ|Generative AI/i, sponsored: /Реклама|Спонсируемые|Sponsored/i, shopping: /Товары|Купить в интернете|Shopping|Цена от|Магазины/i, paa: /Похожие вопросы|People also ask|Вопросы по теме/i, maps: /Карты|Maps|Адрес|Часы работы/i, video: /Видео/i, unusual: /необычн|unusual traffic|подозрительн/i})) marks[k] = re.test(body);
  return {organic, ads, blocks, marks, docH: document.documentElement.scrollHeight, vh: window.innerHeight, title: document.title, url: location.href, bodyLen: body.length};
}
"""

QUERIES = ["винтовой компрессор купить", "адсорбционный осушитель", "азотная установка купить"]
out = {"chromium": find_chromium(), "started": time.strftime("%Y-%m-%d %H:%M:%S"), "results": []}
print("chromium:", out["chromium"], flush=True)
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    kw = {"headless": True}
    if out["chromium"]:
        kw["executable_path"] = out["chromium"]
    browser = p.chromium.launch(**kw)
    ctx = browser.new_context(locale="ru-RU", timezone_id="Europe/Moscow", viewport={"width": 1366, "height": 900})
    page = ctx.new_page()
    try:
        page.goto("https://www.google.com/?hl=ru", timeout=45000, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        out["home_title"] = page.title()
        out["home_url"] = page.url
    except Exception as e:
        out["home_err"] = str(e)[:200]
    for i, qtext in enumerate(QUERIES):
        rec = {"q": qtext}
        slug = "q%d" % (i + 1)
        try:
            url = "https://www.google.com/search?q=%s&hl=ru&gl=ru&pws=0" % urllib.parse.quote(qtext)
            resp = page.goto(url, timeout=45000, wait_until="domcontentloaded")
            rec["status"] = resp.status if resp else None
            page.wait_for_timeout(4000)
            rec["final_url"] = page.url
            rec["title"] = page.title()
            html = page.content()
            rec["html_len"] = len(html)
            low = html.lower()
            rec["blocked"] = ("/sorry/" in page.url) or ("unusual traffic" in low) or ("необычный трафик" in low)
            if rec["blocked"]:
                out["results"].append(rec)
                print("СТОП: Google показал страницу проверки трафика; дальнейшие запросы не делаем.", flush=True)
                break
            try:
                rec["parse"] = page.evaluate(JS)
            except Exception as e:
                rec["parse_err"] = str(e)[:200]
            try:
                put_bytes("R2-serp-test-%s.html.gz" % slug, gzip.compress(html.encode("utf-8")))
                rec["html_drop"] = "R2-serp-test-%s.html.gz" % slug
            except Exception as e:
                rec["html_err"] = str(e)[:100]
            try:
                png = page.screenshot(full_page=True)
                put_bytes("R2-serp-test-%s.png" % slug, png)
                rec["png_drop"] = "R2-serp-test-%s.png" % slug
                rec["png_bytes"] = len(png)
            except Exception as e:
                rec["png_err"] = str(e)[:100]
        except Exception as e:
            rec["error"] = str(e)[:300]
        out["results"].append(rec)
        print(json.dumps({k: v for k, v in rec.items() if k != "parse"}, ensure_ascii=False)[:600], flush=True)
        if rec.get("parse"):
            pz = rec["parse"]
            print("   marks:", pz.get("marks"), "docH", pz.get("docH"), "organic:", len(pz.get("organic", [])), "ads:", len(pz.get("ads", [])))
            for o in pz.get("organic", [])[:12]:
                print("      top=%5s  %s  %s" % (o["top"], o["href"][:70], o["title"][:50]))
            for a in pz.get("ads", [])[:6]:
                print("      AD top=%5s h=%4s links=%s %s" % (a["top"], a["h"], a["links"], a["text"][:100]))
        time.sleep(8)
    browser.close()
put_bytes("R2-serp-test-result.json", json.dumps(out, ensure_ascii=False, indent=1).encode("utf-8"))
print("готово; результат R2-serp-test-result.json")
