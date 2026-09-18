# -*- coding: utf-8 -*-
"""wrap.py inner_X.py out_X.txt <timeout> -> собирает w_inner_X.py.

Обёртка на сервере: перезапускает себя питоном панели (venv seostat), выполняет
вложенный скрипт, ловит весь вывод и кладёт его на дроп под именем out_X.txt.
"""
import sys, os, json

TEMPLATE = '''# -*- coding: utf-8 -*-
import sys, os, io, json, time, traceback, urllib.request

VENV = r"C:\\seostat\\.venv\\Scripts\\python.exe"
if os.path.exists(VENV) and os.path.normcase(sys.executable) != os.path.normcase(VENV):
    os.execv(VENV, [VENV, os.path.abspath(__file__)])

DROP_URL = os.environ.get("DROP_URL", "https://parsercompressor.online/drop").rstrip("/")
DROP_TOKEN = os.environ.get("DROP_TOKEN", "")
OUT_NAME = %(out)r
CODE = %(code)r

buf = io.StringIO()
_so, _se = sys.stdout, sys.stderr
sys.stdout = sys.stderr = buf
t0 = time.time()
try:
    g = {"__name__": "__main__", "__file__": os.path.abspath(__file__)}
    exec(compile(CODE, "inner", "exec"), g)
except SystemExit:
    pass
except Exception:
    traceback.print_exc()
sys.stdout, sys.stderr = _so, _se
body = buf.getvalue()

try:
    req = urllib.request.Request(DROP_URL + "/" + OUT_NAME,
                                 data=body.encode("utf-8", "replace"),
                                 method="PUT",
                                 headers={"X-Drop-Token": DROP_TOKEN})
    urllib.request.urlopen(req, timeout=120).read()
    up = "выгружено на дроп: " + OUT_NAME
except Exception as e:
    up = "НЕ выгружено (%%s)" %% (repr(e)[:120],)
print(json.dumps({"ok": True, "out": OUT_NAME, "bytes": len(body),
                  "took_sec": round(time.time() - t0, 1), "upload": up},
                 ensure_ascii=False))
print(body[-3000:])
'''


def main():
    if len(sys.argv) < 3:
        print("использование: wrap.py inner_X.py out_X.txt [timeout]")
        return 2
    inner, out = sys.argv[1], sys.argv[2]
    timeout = sys.argv[3] if len(sys.argv) > 3 else "900"
    code = open(inner, encoding="utf-8").read()
    dst = "w_" + os.path.basename(inner)
    with open(dst, "w", encoding="utf-8") as f:
        f.write(TEMPLATE % {"out": out, "code": code})
    print("%s таймаут %s" % (dst, timeout))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
