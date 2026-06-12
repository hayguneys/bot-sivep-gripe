# Kestra — SIVEP → Google Sheets (diário)

Agenda o download diário da exportação DBF (última semana) e anexa as linhas novas
a uma planilha Google. Reusa `sivep_core.py` (não modifica o app principal).

## Arquivos
```
flow.yml             workflow Kestra (cron diário + escada de retry 5/10/20 min)
_sivep_kestra.py     helpers (login, gerar, baixar) sobre sivep_core
kestra_gerar.py      task 1: solicita export -> imprime nº da solicitação
kestra_baixar.py     task 2: checa 1x; baixa+extrai se pronto; senão sai !=0 (retry)
kestra_sheets.py     task 3: DBF -> dedup por NU_NOTIFIC -> append no Sheets
kestra_faixa.py      task 4: gera Excel de faixa etária (agregados SG + SRAG_UTI)
kestra_faixa_sheets.py  task 5: Excel faixa etária -> append nas abas *-agregados
```

## Fluxo
`gerar` (solicita) → `espera_inicial` 5min → `baixar` (retry 5→10→20) → `para_sheets`.
Tentativas efetivas de download em ~5, 10, 20 e 40 min após a solicitação.

## Secrets do Kestra (Settings → Secrets)
- `SIVEP_LOGIN`, `SIVEP_SENHA` — credenciais do SIVEP
- `GOOGLE_SERVICE_ACCOUNT` — JSON da service account (string)
- `SHEET_ID` — id da planilha (da URL)

## Google Sheets (uma vez)
1. Crie uma **service account** no Google Cloud, ative a **Google Sheets API**.
2. Gere uma chave JSON → cole em `GOOGLE_SERVICE_ACCOUNT`.
3. **Compartilhe a planilha** com o e-mail da service account (permissão de editor).
4. A aba (`SHEET_TAB`, padrão `dados`) é criada/preenchida automaticamente.

## Namespace files
Suba `sivep_core.py` e a pasta `kestra/` para o namespace `saude.sivep`
(via UI, `kestra namespace files`, ou o git sync do Kestra) para que `namespaceFiles`
encontre os scripts.

## Rodar local (sem Kestra)
```bash
# da raiz do projeto, com o venv ativo e Chromium instalado:
SIVEP_LOGIN=... SIVEP_SENHA=... ANO=2026 TIPO=3 python kestra/kestra_gerar.py
SIVEP_LOGIN=... SIVEP_SENHA=... SOLICITACAO=<num> python kestra/kestra_baixar.py
DBF_FILE=<arquivo.dbf> SHEET_ID=... GOOGLE_SERVICE_ACCOUNT="$(cat sa.json)" \
  python kestra/kestra_sheets.py
```

## Observações
- A imagem `mcr.microsoft.com/playwright/python` já traz o Chromium; o `ensure_chromium`
  vira no-op lá.
- Para baixar **SG** também, duplique as tasks com `TIPO: "1"` (ou faça um loop no flow).
- `kestra_sheets.py` precisa de `gspread`, `dbfread`, `pandas` (instalados em `beforeCommands`).
