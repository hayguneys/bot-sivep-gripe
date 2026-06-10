# bot-sivep-gripe

Baixa exportações DBF (SRAG Hospitalizado e SG) do SIVEP-Gripe via Playwright.

## Estrutura
```
sivep_core.py   lógica (login, gera, baixa, extrai)
sivep_ui.py     GUI Qt6 (Runner + visualizador DBF)
setup.* / run.* instala e executa
downloads/      arquivos .dbf
```

## Lógica
Login → EXPORTAÇÃO ▸ Registros Individuais → ficha + ano → Gerar Arquivo →
Consultar Exportações DBF → aguarda "Concluído" → baixa e extrai o .dbf.

## Uso
Baixe o `.exe` em **Releases** (baixa o Chromium na 1ª execução) ou rode do código:
`setup` e depois `run`. Credenciais na GUI (salvas em `.env`).
