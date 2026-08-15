"""
collect.py — coleta preços do PS5 Slim das lojas em sites.json
e grava historico.json / ultimo_status.json.

Fontes por loja, em cascata (a primeira que retornar preço vence):
  1. JSON-LD (schema.org Product/Offer)
  2. Meta tags (product:price:amount, og:price, itemprop=price)
  3. Seletor CSS configurado
  4. Texto completo da página (regex de moeda)

Se nenhuma funcionar e o Playwright estiver disponível, a página é renderizada
em browser headless e a cascata é repetida (útil para lojas React/VTEX).

Lojas com "tipo": "agregador" (Zoom/Buscapé) têm os preços de várias lojas
extraídos de uma só vez, gerando uma linha por loja.

Uso:
    python collect.py            # roda uma vez
    python collect.py --watch    # fica rodando (padrão: a cada 12h)
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("[ERRO] Dependências não instaladas. Rode: pip install -r requirements.txt")
    sys.exit(1)

try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except Exception:
    HAS_PLAYWRIGHT = False

BASE_DIR       = Path(__file__).resolve().parent
SITES_FILE     = BASE_DIR / "sites.json"
HISTORICO_FILE = BASE_DIR / "historico.json"
STATUS_FILE    = BASE_DIR / "ultimo_status.json"

MAX_HISTORICO = 20000

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                return default
            return json.loads(content)
    except Exception:
        return default


def save_json(path: Path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_sites():
    sites = load_json(SITES_FILE, None)
    if sites is None:
        print(f"[ERRO] {SITES_FILE} não encontrado.")
        sys.exit(1)
    return sites


def parse_price(text) -> float | None:
    if not text:
        return None
    text = text.replace("\xa0", " ").strip()
    match = re.search(r"\d+(?:[.,]\d{3})*(?:[.,]\d{2})?", text)
    if not match:
        return None
    raw = match.group(0)
    if re.search(r"\.\d{3},\d{2}$", raw):   # BR: 3.499,00
        raw = raw.replace(".", "").replace(",", ".")
    elif re.search(r",\d{3}\.\d{2}$", raw): # US: 3,499.00
        raw = raw.replace(",", "")
    elif re.search(r",\d{2}$", raw):        # 3499,00
        raw = raw.replace(",", ".")
    elif re.search(r"\.\d{3}$", raw):       # 3.499 (milhar sem centavos)
        raw = raw.replace(".", "")
    try:
        return float(raw)
    except ValueError:
        return None


def extract_price_from_jsonld(soup):
    def walk(value):
        if isinstance(value, dict):
            if value.get("@type") in ("Offer", "http://schema.org/Offer") and "price" in value:
                return str(value["price"])
            for item in value.values():
                found = walk(item)
                if found:
                    return found
        elif isinstance(value, list):
            for item in value:
                found = walk(item)
                if found:
                    return found
        return None

    for script in soup.find_all("script", type="application/ld+json"):
        text = script.string
        if not text:
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        price_text = walk(data)
        if price_text:
            p = parse_price(price_text)
            if p is not None:
                return p
    return None


def extract_price_from_meta(soup):
    for key in ("product:price:amount", "og:price:amount", "og:price"):
        tag = soup.find("meta", attrs={"property": key}) or soup.find("meta", attrs={"name": key})
        if tag and tag.get("content"):
            p = parse_price(tag["content"])
            if p is not None:
                return p
    el = soup.find(attrs={"itemprop": "price"})
    if el:
        content = el.get("content") or el.get_text()
        p = parse_price(content)
        if p is not None:
            return p
    return None


def find_matching_element(soup, selector):
    if isinstance(selector, list):
        for item in selector:
            el = soup.select_one(item)
            if el is not None:
                return el
        return None
    if isinstance(selector, str):
        for item in [s.strip() for s in selector.split(",") if s.strip()]:
            el = soup.select_one(item)
            if el is not None:
                return el
        return None
    return None


def extract_price_from_text(soup):
    text = soup.get_text(" ", strip=True)
    if not text:
        return None
    # Só aceita valores precedidos por "R$" (evita confundir com contagens, datas etc.)
    for m in re.finditer(r"R\$\s*([\d][\d.,]*\d)", text):
        p = parse_price(m.group(1))
        if p is not None and p >= 100:
            return p
    return None


def price_from_soup(soup, selector, allow_text=False) -> float | None:
    p = extract_price_from_jsonld(soup)
    if p is not None:
        return p
    p = extract_price_from_meta(soup)
    if p is not None:
        return p
    if selector and selector != "body":
        el = find_matching_element(soup, selector)
        if el is not None:
            p = parse_price(el.get_text())
            if p is not None:
                return p
    if allow_text:
        return extract_price_from_text(soup)
    return None


def fetch_html(url: str) -> str:
    last_error = None
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            if r.status_code == 403:
                raise requests.exceptions.HTTPError("HTTP 403")
            r.raise_for_status()
            return r.text
        except Exception as e:
            last_error = e
            time.sleep(2 * (attempt + 1))
    raise last_error


def fetch_html_playwright(url: str) -> str | None:
    if not HAS_PLAYWRIGHT:
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            ctx = browser.new_context(
                user_agent=HEADERS["User-Agent"],
                locale="pt-BR",
                viewport={"width": 1366, "height": 768},
            )
            ctx.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            )
            page = ctx.new_page()
            page.goto(url, timeout=30000, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
            html = page.content()
            browser.close()
            return html
    except Exception:
        return None


def new_result(nome, url):
    return {
        "nome": nome,
        "url": url,
        "preco": None,
        "status": "erro",
        "mensagem": "",
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }


def normalize_store(name):
    if not name:
        return name
    name = re.sub(r"^Via\s+", "", name)
    name = name.strip().rstrip("!")
    return re.sub(r"\s+", " ", name)


def is_ps5_console(nome):
    n = nome.lower()
    excluidos = (
        "portal", "jogo", "controle", "headset", "volante", "capa", "base",
        "carregador", "suporte", "cabo", "hdmi", "pelicula", "película",
        "grip", "stand", "acessorio", "acessório", "fone", "mouse", "teclado",
        "case", "ssd externo", "flash", "pen drive", "pendrive", "cartão", "cartao",
    )
    for w in excluidos:
        if w in n:
            return False
    if "playstation 5" in n or "playstation5" in n:
        return True
    if "ps5" in n and "console" in n:
        return True
    return False


def scrape_agregador(site) -> list:
    fonte = site.get("nome", "Agregador")
    url_busca = site.get("url", "")
    resultados = []

    try:
        html = fetch_html(url_busca)
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select('article[data-testid="product-card"]')

        ofertas = {}
        for card in cards:
            nm = card.select_one('[data-testid="product-card::name"]')
            pr = card.select_one('[data-testid="product-card::price"]')
            if not nm or not pr:
                continue
            nome_produto = nm.get_text(" ", strip=True)
            if not is_ps5_console(nome_produto):
                continue
            preco = parse_price(pr.get_text())
            if preco is None:
                continue

            loja = None
            best = card.select_one('[aria-label="Menor preço"]')
            if best:
                loja = normalize_store(best.get_text(" ", strip=True))
            else:
                merchant = card.select_one('[data-area="merchant"]')
                if merchant:
                    loja = normalize_store(merchant.get_text(" ", strip=True))
            if not loja:
                continue

            link = card.select_one('a[href]')
            url = urljoin(url_busca, link["href"]) if link else url_busca

            if loja not in ofertas or preco < ofertas[loja]["preco"]:
                ofertas[loja] = {"preco": preco, "url": url, "produto": nome_produto}

        for loja, data in ofertas.items():
            r = new_result(f"{loja} ({fonte})", data["url"])
            r["preco"] = data["preco"]
            r["status"] = "ok"
            resultados.append(r)
            print(f"  OK {loja} ({fonte}): R$ {data['preco']:,.2f}")

        if not resultados:
            r = new_result(fonte, url_busca)
            r["mensagem"] = "Nenhum produto relevante encontrado"
            r["status"] = "seletor_falhou"
            resultados.append(r)
    except requests.exceptions.Timeout:
        r = new_result(fonte, url_busca)
        r["mensagem"] = "Timeout"
        resultados.append(r)
    except Exception as e:
        r = new_result(fonte, url_busca)
        r["mensagem"] = str(e)[:140]
        resultados.append(r)

    if resultados and resultados[0]["status"] == "erro":
        print(f"  ERRO {fonte}: {resultados[0]['mensagem']}")

    return resultados


def scrape_site(site) -> dict:
    nome = site.get("nome", "?")
    url  = site.get("url", "")
    sel  = site.get("seletor", "")

    resultado = new_result(nome, url)

    if not url:
        resultado["mensagem"] = "URL não configurada"
        return resultado

    try:
        html = fetch_html(url)
        soup = BeautifulSoup(html, "html.parser")
        preco = price_from_soup(soup, sel, allow_text=(sel == "body"))
        origem = "requests"
        if preco is None:
            rendered = fetch_html_playwright(url)
            if rendered:
                soup = BeautifulSoup(rendered, "html.parser")
                preco = price_from_soup(soup, sel, allow_text=True)
                origem = "playwright"

        if preco is None:
            resultado["mensagem"] = "Preço não encontrado (cascata de extração esgotada)"
            resultado["status"] = "seletor_falhou"
        else:
            resultado["preco"] = preco
            resultado["status"] = "ok"
            print(f"  OK {nome}: R$ {preco:,.2f} [{origem}]")

    except requests.exceptions.Timeout:
        resultado["mensagem"] = "Timeout"
    except Exception as e:
        resultado["mensagem"] = str(e)[:140]

    if resultado["status"] == "erro":
        print(f"  ERRO {nome}: {resultado['mensagem']}")

    return resultado


def run_once():
    sites = load_sites()
    historico = load_json(HISTORICO_FILE, [])
    rodada_ts = datetime.now().isoformat(timespec="seconds")

    print(f"\n[{rodada_ts}] Buscando preços em {len(sites)} fonte(s)...")

    resultados = []
    for site in sites:
        if site.get("tipo") == "agregador":
            resultados.extend(scrape_agregador(site))
        else:
            resultados.append(scrape_site(site))
        time.sleep(1.5)

    novos = 0
    for r in resultados:
        if r.get("preco") is not None:
            historico.append({
                "timestamp": r["timestamp"],
                "nome": r["nome"],
                "url": r["url"],
                "preco": r["preco"],
            })
            novos += 1

    if len(historico) > MAX_HISTORICO:
        historico = historico[-MAX_HISTORICO:]

    save_json(HISTORICO_FILE, historico)
    save_json(STATUS_FILE, {"rodada": rodada_ts, "resultados": resultados})

    print(f"  -> {novos} preço(s) salvos. Histórico total: {len(historico)} registros.\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch", action="store_true", help="Roda continuamente")
    parser.add_argument("--intervalo", type=int, default=12,
                        help="Intervalo em horas para --watch (padrão: 12)")
    args = parser.parse_args()

    if args.watch:
        print(f"Modo watch: atualizando a cada {args.intervalo}h. Ctrl+C para parar.")
        while True:
            run_once()
            time.sleep(args.intervalo * 3600)
    else:
        run_once()


if __name__ == "__main__":
    main()
