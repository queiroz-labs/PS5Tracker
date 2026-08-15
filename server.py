"""
server.py — servidor local para o PS5 Tracker
Serve o index.html e expõe APIs para o frontend consumir.

Uso:
    python server.py
    # Abre http://localhost:8765 no browser
"""

import json
import subprocess
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

PORT = 8765
BASE_DIR = Path(__file__).resolve().parent
HISTORICO_FILE   = BASE_DIR / "historico.json"
STATUS_FILE      = BASE_DIR / "ultimo_status.json"
SITES_FILE       = BASE_DIR / "sites.json"
INDEX_FILE       = BASE_DIR / "index.html"


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


class Handler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        # Silencia logs do servidor no terminal (deixa mais limpo)
        pass

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, path: Path):
        content = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(content))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        parsed = urlparse(self.path)
        path   = parsed.path.rstrip("/") or "/"

        # ── Página principal ──────────────────────────────────────
        if path == "/" or path == "/index.html":
            if INDEX_FILE.exists():
                self.send_html(INDEX_FILE)
            else:
                self.send_json({"erro": "index.html não encontrado"}, 404)

        # ── Histórico completo ────────────────────────────────────
        elif path == "/api/historico":
            self.send_json(load_json(HISTORICO_FILE, []))

        # ── Último status do scraper (com erros) ──────────────────
        elif path == "/api/status":
            self.send_json(load_json(STATUS_FILE, {}))

        # ── Sites configurados ────────────────────────────────────
        elif path == "/api/sites":
            self.send_json(load_json(SITES_FILE, []))

        # ── Arquivos estáticos (mesmos que o GitHub Pages serve) ───
        elif path == "/historico.json":
            self.send_json(load_json(HISTORICO_FILE, []))
        elif path == "/ultimo_status.json":
            self.send_json(load_json(STATUS_FILE, {}))
        elif path == "/sites.json":
            self.send_json(load_json(SITES_FILE, []))

        # ── Dispara o scraper manualmente ─────────────────────────
        elif path == "/api/atualizar":
            try:
                resultado = subprocess.run(
                    [sys.executable, str(BASE_DIR / 'collect.py')],
                    capture_output=True, text=True, timeout=180,
                    cwd=str(BASE_DIR)
                )
                ok = resultado.returncode == 0
                self.send_json({
                    "ok": ok,
                    "stdout": resultado.stdout[-2000:],
                    "stderr": resultado.stderr[-500:],
                })
            except subprocess.TimeoutExpired:
                self.send_json({"ok": False, "erro": "Timeout (60s)"})
            except Exception as e:
                self.send_json({"ok": False, "erro": str(e)})

        else:
            self.send_json({"erro": "Rota não encontrada"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path   = parsed.path

        # ── Salva sites.json ──────────────────────────────────────
        if path == "/api/sites":
            length = int(self.headers.get("Content-Length", 0))
            body   = self.rfile.read(length)
            try:
                sites = json.loads(body)
                with open(SITES_FILE, "w", encoding="utf-8") as f:
                    json.dump(sites, f, ensure_ascii=False, indent=2)
                self.send_json({"ok": True})
            except Exception as e:
                self.send_json({"ok": False, "erro": str(e)}, 400)

        # ── Limpa histórico ───────────────────────────────────────
        elif path == "/api/historico/limpar":
            with open(HISTORICO_FILE, "w", encoding="utf-8") as f:
                json.dump([], f)
            self.send_json({"ok": True})

        else:
            self.send_json({"erro": "Rota não encontrada"}, 404)


def main():
    server = HTTPServer(("localhost", PORT), Handler)
    url    = f"http://localhost:{PORT}"
    print(f"""
╔══════════════════════════════════════════╗
║   PS5 Slim Tracker — servidor local      ║
╠══════════════════════════════════════════╣
║  Aberto em: {url}          ║
║  Ctrl+C para parar                       ║
╚══════════════════════════════════════════╝
""")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor encerrado.")


if __name__ == "__main__":
    main()
