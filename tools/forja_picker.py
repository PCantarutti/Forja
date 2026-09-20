"""forja-picker: abre o seletor de pasta NATIVO do sistema para a interface do Forja.

O Forja roda no Docker e a interface roda no navegador; nenhum dos dois consegue abrir o Explorer
e devolver o caminho completo de uma pasta. Este ajudante roda no seu sistema (fora do Docker),
escuta só em 127.0.0.1 e só atende a própria interface do Forja (checagem de Origin).

    Windows:  pythonw tools\\forja_picker.py        (sem janela de console)
    Linux:    python3 tools/forja_picker.py &
    macOS:    python3 tools/forja_picker.py &

Diálogo usado:
    Windows -> Tk 8.6 (IFileOpenDialog: o "Selecionar pasta" do Explorer)
    Linux   -> zenity (GNOME) ou kdialog (KDE); sem eles, Tk
    macOS   -> osascript "choose folder"

Só usa a biblioteca padrão. Variáveis: FORJA_PICKER_PORT (3001), FORJA_ORIGINS
("http://localhost:7001,http://127.0.0.1:7001").
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PORT = int(os.getenv("FORJA_PICKER_PORT", "3001"))
ORIGINS = {o.strip().rstrip("/") for o in
           os.getenv("FORJA_ORIGINS", "http://localhost:7001,http://127.0.0.1:7001").split(",") if o.strip()}
TITLE = "Forja: escolher a pasta de trabalho"
SYSTEM = platform.system()
_dialog_lock = threading.Lock()  # um diálogo por vez


def _tk_pick(start: str | None) -> str | None:
    import tkinter
    from tkinter import filedialog

    root = tkinter.Tk()
    root.withdraw()
    root.attributes("-topmost", True)  # o diálogo precisa aparecer na frente do navegador
    root.lift()
    root.focus_force()
    try:
        path = filedialog.askdirectory(parent=root, title=TITLE, initialdir=start or None, mustexist=True)
    finally:
        root.destroy()
    return path or None


def _linux_pick(start: str | None) -> str | None:
    if shutil.which("zenity"):
        cmd = ["zenity", "--file-selection", "--directory", f"--title={TITLE}"]
        if start:
            cmd.append(f"--filename={start.rstrip('/')}/")
    elif shutil.which("kdialog"):
        cmd = ["kdialog", "--title", TITLE, "--getexistingdirectory", start or os.path.expanduser("~")]
    else:
        return _tk_pick(start)
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.stdout.strip() or None  # código != 0 = cancelado


def _mac_pick(start: str | None) -> str | None:
    script = f'POSIX path of (choose folder with prompt "{TITLE}"'
    if start and os.path.isdir(start):
        script += f' default location POSIX file "{start}"'
    script += ")"
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return r.stdout.strip().rstrip("/") or None


def pick(start: str | None) -> str | None:
    if start and not os.path.isdir(start):
        start = None
    if SYSTEM == "Linux":
        return _linux_pick(start)
    if SYSTEM == "Darwin":
        return _mac_pick(start)
    return _tk_pick(start)


class Handler(BaseHTTPRequestHandler):
    def _origin_ok(self) -> bool:
        return self.headers.get("Origin", "").rstrip("/") in ORIGINS

    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        if self._origin_ok():
            self.send_header("Access-Control-Allow-Origin", self.headers["Origin"])
            self.send_header("Vary", "Origin")
            # Chrome: página em localhost falando com outro serviço local
            self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):  # preflight
        self._send(204 if self._origin_ok() else 403, {})

    def do_GET(self):
        # Qualquer site aberto no navegador poderia chamar 127.0.0.1:3001; só a interface do Forja passa.
        if not self._origin_ok():
            return self._send(403, {"error": "origem não permitida"})
        url = urlparse(self.path)
        if url.path == "/ping":
            return self._send(200, {"ok": True, "system": SYSTEM})
        if url.path != "/pick":
            return self._send(404, {"error": "rota desconhecida"})
        start = (parse_qs(url.query).get("start") or [None])[0]
        if not _dialog_lock.acquire(blocking=False):
            return self._send(409, {"error": "já existe um seletor de pasta aberto"})
        try:
            path = pick(start)
        except Exception as e:  # sem ambiente gráfico, Tk ausente etc.
            return self._send(500, {"error": f"{e.__class__.__name__}: {e}"})
        finally:
            _dialog_lock.release()
        self._send(200, {"path": path, "cancelled": path is None, "system": SYSTEM})

    def log_message(self, *args):  # silencioso (roda com pythonw, sem console)
        pass


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"forja-picker ouvindo em http://127.0.0.1:{PORT} (origens: {', '.join(sorted(ORIGINS))})")
    server.serve_forever()


if __name__ == "__main__":
    main()
