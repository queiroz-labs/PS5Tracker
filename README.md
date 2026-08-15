# PS5 Slim Price Tracker

Rastreador de preços do PS5 Slim com histórico, gráfico e deploy gratuito
(GitHub Actions + GitHub Pages). Atualiza a cada 12 horas automaticamente.

## Estrutura do projeto

```
ps5tracker/
├── index.html                     # interface web (estática)
├── collect.py                     # coleta preços (sites + Mercado Livre) e salva JSONs
├── server.py                      # servidor local opcional para desenvolvimento
├── sites.json                     # lojas monitoradas
├── requirements.txt               # dependências Python
├── .github/workflows/tracker.yml  # cron de 12h + commit automático
├── historico.json                 # gerado automaticamente
├── ultimo_status.json             # gerado automaticamente
└── README.md
```

## Como funciona

O `collect.py` busca o preço em cada loja de `sites.json` usando uma cascata de
extração (JSON-LD → meta tags → seletor CSS → regex) e, se necessário, renderiza
a página com Playwright (browser headless) para lojas com JavaScript. Também
consulta a **API pública do Mercado Livre** como fonte extra.

No GitHub Actions, o workflow `tracker.yml` roda a cada 12h (`cron: 0 */12 * * *`),
grava `historico.json`/`ultimo_status.json` e commita de volta. O GitHub Pages
serve o `index.html` + os JSONs — sem servidor.

## Deploy (uma única vez)

1. Crie um repositório **público** no GitHub e faça push deste código.
2. Em **Settings → Pages**, ative *Deploy from a branch* → `main` → `/ (root)`.
3. Pronto. O site fica em `https://<seu-usuario>.github.io/<repo>/` e o
   agendamento começa a rodar automaticamente.

### Mercado Livre (opcional, recomendado)

A consulta anônima ao Mercado Livre pode ser bloqueada por IP de datacenter
(erro 403). Para contornar, crie um app gratuito em
<https://developers.mercadolibre.com/> e adicione como segredos do repositório
(Settings → Secrets and variables → Actions):

- `ML_CLIENT_ID`
- `ML_CLIENT_SECRET`

O `collect.py` então obtém um token OAuth (`client_credentials`) e usa a API com
autorização. Sem os segredos, a coleta do Mercado Livre é feita de forma anônima.

## Uso local

```powershell
python -m pip install -r requirements.txt
python collect.py              # roda uma vez
python collect.py --watch      # roda continuamente (padrão: 12h)
```

Para visualizar a interface localmente:

```powershell
python server.py               # abre http://localhost:8765
```

## Configuração de lojas (`sites.json`)

Cada loja tem:

- `nome`: nome exibido
- `url`: página do produto
- `seletor`: seletor CSS do preço, `"jsonld"` (usar dados estruturados), ou
  deixar vazio para usar apenas a cascata automática.

A extração já tenta JSON-LD e meta tags antes do seletor, então a maioria das
lojas funciona sem configurar seletor manual.
