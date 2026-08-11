#!/usr/bin/env python3
"""Устанавливает вкладку «Директ» в дерево панели SEO Статистики.

Что делает (идемпотентно — повторный запуск ничего не ломает):
  1. копирует 4 новых файла в дерево ``app/`` панели;
  2. патчит ``app/main.py``  — регистрирует роутер ``routes_direct``;
  3. патчит ``app/templates/partials/nav.html`` — добавляет пункт меню «Директ».

Запуск на сервере, из папки распакованного пакета:

    python apply_direct_tab.py --app-root C:\\seostat        (или путь, где лежит app/)

По умолчанию ``--app-root`` = текущая папка. Скрипт делает .bak-копии изменяемых
файлов и печатает, что именно поменял. После него — обычный перезапуск службы.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# новые файлы: (откуда в пакете) -> (куда в дереве панели, относительно app-root)
NEW_FILES = [
    ("app/providers/yandex_direct.py", "app/providers/yandex_direct.py"),
    ("app/services/direct.py", "app/services/direct.py"),
    ("app/services/direct_store.py", "app/services/direct_store.py"),
    ("app/services/direct_collect.py", "app/services/direct_collect.py"),
    ("app/services/direct_changes.py", "app/services/direct_changes.py"),
    ("app/db/models_direct.py", "app/db/models_direct.py"),
    ("app/api/routes_direct.py", "app/api/routes_direct.py"),
    ("app/templates/direct.html", "app/templates/direct.html"),
]

# Врезка ночного сбора Директа в общий прогон панели. Отдельный try — чтобы
# отвалившийся Директ (протух токен, не одобрена заявка) не ронял сбор GSC и
# Вебмастера, который идёт в той же функции.
JOBS_HOOK = """
    # --- Яндекс Директ: собственная история по доменам (добавлено вкладкой «Директ») ---
    try:
        from app.services import direct_collect
        direct_collect.run_daily(db)
    except Exception:  # noqa: BLE001 - Директ не должен ронять сбор поисковых источников
        logger.exception("Директ: ночной сбор не удался")
"""


def _backup(path: Path) -> None:
    bak = path.with_suffix(path.suffix + ".bak-direct")
    if not bak.exists():
        shutil.copy2(path, bak)
        print(f"  бэкап: {bak.name}")


def copy_new_files(app_root: Path) -> None:
    print("• Копирую новые файлы:")
    for src_rel, dst_rel in NEW_FILES:
        src = HERE / src_rel
        dst = app_root / dst_rel
        if not src.exists():
            sys.exit(f"НЕ найден файл пакета: {src}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        print(f"  → {dst_rel}")


def patch_main(app_root: Path) -> None:
    path = app_root / "app" / "main.py"
    if not path.exists():
        sys.exit(f"НЕ найден {path}")
    text = path.read_text(encoding="utf-8")
    original = text

    # 1) добавить routes_direct в блок `from app.api import ( ... )`
    if "routes_direct" not in text:
        m = re.search(r"from app\.api import \(([^)]*)\)", text, re.S)
        if not m:
            sys.exit("main.py: не найден импорт `from app.api import (...)` — впишите routes_direct вручную.")
        block = m.group(1)
        # добавляем строкой, сохраняя отступ существующих элементов
        indent = re.search(r"\n(\s*)\w", block)
        pad = indent.group(1) if indent else "    "
        new_block = block.rstrip() + f"\n{pad}routes_direct,\n"
        text = text[:m.start(1)] + new_block + text[m.end(1):]

    # 2) добавить routes_direct в кортеж `for module in ( ... )`
    m2 = re.search(r"for module in \(([^)]*)\):", text, re.S)
    if not m2:
        sys.exit("main.py: не найден цикл `for module in (...)` — впишите routes_direct вручную.")
    if "routes_direct" not in m2.group(1):
        block = m2.group(1)
        indent = re.search(r"\n(\s*)\w", block)
        pad = indent.group(1) if indent else "        "
        new_block = block.rstrip() + f"\n{pad}routes_direct,\n{pad[:-4] if len(pad) >= 4 else pad}"
        text = text[:m2.start(1)] + new_block + text[m2.end(1):]

    if text == original:
        print("• app/main.py — уже настроен, пропускаю.")
        return
    _backup(path)
    path.write_text(text, encoding="utf-8")
    print("• app/main.py — роутер routes_direct зарегистрирован.")


def patch_nav(app_root: Path) -> None:
    path = app_root / "app" / "templates" / "partials" / "nav.html"
    if not path.exists():
        sys.exit(f"НЕ найден {path}")
    text = path.read_text(encoding="utf-8")
    if "/direct" in text:
        print("• nav.html — пункт «Директ» уже есть, пропускаю.")
        return

    item = '    <li><a href="{{ base_path }}/direct">Директ</a></li>\n'
    # ставим сразу после пункта «Ошибки 404», перед выпадающим меню
    anchor = re.search(r'(\n\s*<li><a href="\{\{ base_path \}\}/errors">[^<]*</a></li>\n)', text)
    if anchor:
        text = text[:anchor.end(1)] + item + text[anchor.end(1):]
    else:
        # запасной вариант — перед выпадающим «Расширенный функционал»
        anchor2 = re.search(r'\n(\s*<li>\s*\n\s*<details class="dropdown">)', text)
        if not anchor2:
            sys.exit("nav.html: не нашёл, куда вставить пункт — добавьте <li>…/direct…</li> вручную.")
        text = text[:anchor2.start()] + "\n" + item + text[anchor2.start() + 1:]

    _backup(path)
    path.write_text(text, encoding="utf-8")
    print("• nav.html — добавлен пункт меню «Директ».")


def patch_jobs(app_root: Path) -> None:
    """Врезает сбор Директа в конец ``run_daily_collect`` планировщика панели."""
    path = app_root / "app" / "scheduler" / "jobs.py"
    if not path.is_file():
        print("• scheduler/jobs.py не найден — ночной сбор Директа не подключён.")
        return
    text = path.read_text(encoding="utf-8")
    if "direct_collect.run_daily" in text:
        print("• scheduler/jobs.py — ночной сбор Директа уже подключён, пропускаю.")
        return

    start = text.find("def run_daily_collect(")
    if start == -1:
        print("• scheduler/jobs.py: не найдена run_daily_collect — подключите сбор Директа вручную.")
        return
    # первый `return results` внутри этой функции — её выход
    marker = "\n    return results\n"
    pos = text.find(marker, start)
    if pos == -1:
        print("• scheduler/jobs.py: не найден выход из run_daily_collect — подключите вручную.")
        return

    _backup(path)
    text = text[:pos] + "\n" + JOBS_HOOK + text[pos:]
    path.write_text(text, encoding="utf-8")
    print("• scheduler/jobs.py — ночной сбор Директа подключён.")


def main() -> int:
    ap = argparse.ArgumentParser(description="Установка вкладки «Директ» в панель.")
    ap.add_argument("--app-root", default=".",
                    help="Папка, содержащая каталог app/ (корень панели). По умолчанию — текущая.")
    args = ap.parse_args()

    app_root = Path(args.app_root).resolve()
    if not (app_root / "app").is_dir():
        sys.exit(f"В {app_root} нет каталога app/. Укажите корень панели через --app-root.")

    print(f"Корень панели: {app_root}\n")
    copy_new_files(app_root)
    patch_main(app_root)
    patch_nav(app_root)
    patch_jobs(app_root)
    print("\nГотово. Осталось перезапустить службу панели, чтобы вкладка появилась.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
