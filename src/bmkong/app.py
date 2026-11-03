"""Desktop shell (pywebview) around web/index.html. `--serve` exposes the same API over HTTP for development."""
from __future__ import annotations

import json
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import ROOT
from .container import SaveError
from .save import APP_DIR, SaveFile, find_saves, game_running

WEB_DIR = ROOT / "bmkong" / "web"
SETTINGS = APP_DIR / "settings.json"
TITLES = {"zh": "黑神话：悟空存档编辑", "en": "Black Myth: Wukong Save Editor"}
API = ("candidates", "status", "set_lang", "open", "reload", "write", "open_backups")


def _settings() -> dict:
    try:
        return json.loads(SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _remember(**values) -> None:
    data = _settings() | values
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def _result(fn):
    """Return {ok, data} / {ok: false, error} so the page shows readable errors."""
    def wrapper(*args, **kwargs):
        try:
            return {"ok": True, "data": fn(*args, **kwargs)}
        except SaveError as exc:
            return {"ok": False, "error": exc.text(args[0]._lang if args else "zh")}
        except Exception as exc:  # unexpected: keep the window alive and show the cause
            traceback.print_exc()
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    wrapper.__name__ = fn.__name__
    return wrapper


class Api:
    def __init__(self):
        self._save: SaveFile | None = None
        self._window = None
        self._lang = _settings().get("lang") or "zh"

    @_result
    def candidates(self):
        last = _settings().get("last_save")
        found = [str(p) for p in find_saves()]
        if last and Path(last).is_file() and last not in found:
            found.insert(0, last)
        return {"last": last if last and Path(last).is_file() else None, "saves": found,
                "lang": _settings().get("lang")}

    @_result
    def set_lang(self, lang, remember=False):
        """Language for error messages and the window title; `remember` persists a choice made in the UI."""
        self._lang = "en" if lang == "en" else "zh"
        if remember:
            _remember(lang=self._lang)
        if self._window:
            self._window.set_title(TITLES[self._lang])
        return self._lang

    @_result
    def status(self):
        return {"running": game_running()}

    @_result
    def open(self, path):
        self._save = SaveFile(path)
        _remember(last_save=str(self._save.path))
        return self._save.summary()

    @_result
    def reload(self):
        if not self._save:
            raise SaveError("尚未打开存档。", "No save is open.")
        self._save = SaveFile(self._save.path)
        return self._save.summary()

    @_result
    def browse(self):
        import webview
        start = str(self._save.path.parent) if self._save else ""
        picked = self._window.create_file_dialog(webview.OPEN_DIALOG, directory=start,
                                                file_types=("存档文件 (*.sav)", "所有文件 (*.*)"))
        return picked[0] if picked else None

    @_result
    def write(self, changes):
        if not self._save:
            raise SaveError("尚未打开存档。", "No save is open.")
        result = self._save.write(changes)
        self._save = SaveFile(self._save.path)
        return result | {"summary": self._save.summary()}

    @_result
    def open_backups(self):
        folder = APP_DIR / "backups"
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(folder)
        return str(folder)


def run_window():
    import webview
    api = Api()
    api._window = webview.create_window(TITLES[api._lang], str(WEB_DIR / "index.html"), js_api=api,
                                       width=1180, height=820, min_size=(900, 640), background_color="#0e0d0b")
    webview.start(icon=str(ROOT / "assets" / "icon.ico"))


def run_server(port: int = 8766):
    api = Api()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path not in ("/", "/index.html"):
                self.send_error(404)
                return
            body = (WEB_DIR / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            method = self.path.removeprefix("/api/")
            if method not in API:
                self.send_error(404)
                return
            args = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"[]")
            body = json.dumps(getattr(api, method)(*args), ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"http://127.0.0.1:{port}", flush=True)
    server.serve_forever()


def main():
    if "--serve" in sys.argv:
        run_server()
    else:
        run_window()
