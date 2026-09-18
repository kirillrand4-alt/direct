# -*- coding: utf-8 -*-
"""push.py w_inner_X.py [--timeout N] — залить скрипт на сервер и запустить.

Два задания раннеру: panel_file_put (положить под C:\\sender\\_ops) и panel_py
(запустить питоном панели). Подпись HMAC по JOB_SECRET: сначала из окружения,
иначе из .runner-secrets.env рядом с этим файлом.
"""
import sys, os, re, json, time, hmac, hashlib, random, urllib.request

DROP_URL = os.environ.get("DROP_URL", "https://parsercompressor.online/drop").rstrip("/")
DROP_TOKEN = os.environ.get("DROP_TOKEN", "")
HERE = os.path.dirname(os.path.abspath(__file__))


def job_secret():
    s = os.environ.get("JOB_SECRET", "")
    if s:
        return s
    p = os.path.join(HERE, ".runner-secrets.env")
    if os.path.exists(p):
        for ln in open(p, encoding="utf-8-sig"):
            m = re.match(r"\s*JOB_SECRET\s*=\s*(\S+)", ln)
            if m:
                return m.group(1)
    return ""


SECRET = job_secret()


def req(method, path, data=None, timeout=120):
    r = urllib.request.Request("%s/%s" % (DROP_URL, path), data=data, method=method,
                               headers={"X-Drop-Token": DROP_TOKEN})
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return resp.read()


def sign(job):
    payload = {"id": job["id"], "task": job["task"], "args": job["args"], "ts": job["ts"]}
    canon = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hmac.new(SECRET.encode(), canon.encode("utf-8"), hashlib.sha256).hexdigest()


def send(args, wait=1800, label=""):
    jid = "%d-%d-%d" % (int(time.time()), os.getpid(), random.randint(1000, 99999))
    job = {"id": jid, "task": "enrich_contacts", "args": args, "ts": int(time.time())}
    job["sig"] = sign(job)
    req("PUT", "job-%s.json" % jid,
        json.dumps(job, ensure_ascii=False).encode("utf-8"))
    t0 = time.time()
    while time.time() - t0 < wait:
        try:
            blob = req("GET", "result-%s.json" % jid, timeout=60)
            res = json.loads(blob.decode("utf-8", "replace"))
            try:
                req("DELETE", "result-%s.json" % jid)
            except Exception:
                pass
            return res
        except Exception:
            time.sleep(10)
            if int(time.time() - t0) % 120 < 10:
                print("  ждём результат%s..." % (" " + label if label else ""), flush=True)
    return {"ok": False, "error": "клиент не дождался за %ds" % wait}


def main():
    if len(sys.argv) < 2:
        print("использование: push.py w_inner_X.py [--timeout N]")
        return 2
    path = sys.argv[1]
    timeout = 900
    if "--timeout" in sys.argv:
        timeout = int(sys.argv[sys.argv.index("--timeout") + 1])
    name = os.path.basename(path)
    body = open(path, "rb").read()
    dest = "C:\\sender\\_ops\\" + name
    req("PUT", name, body)
    r1 = send({"op": "panel_file_put",
               "files": [{"drop": name, "dest": dest}]}, wait=600, label="загрузку")
    if not (r1.get("ok", True)) or r1.get("error"):
        print(json.dumps(r1, ensure_ascii=False)[:600])
        return 1
    r2 = send({"op": "panel_py", "script": dest, "argv": [], "timeout": timeout},
              wait=timeout + 300, label="выполнение")
    tail = r2.get("stdout_tail") or ""
    print(tail[-4000:] if tail else json.dumps(r2, ensure_ascii=False)[:1500])
    if r2.get("stderr_tail"):
        print("--- stderr ---")
        print(r2["stderr_tail"][-1200:])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
