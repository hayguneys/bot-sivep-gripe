"""Kestra task 2 — check ONCE if the export is ready; download + extract if so.

Single check, no internal polling: if the export is not yet concluded the script
exits non-zero so Kestra's retry ladder (5 -> 10 -> 20 min) takes over.

Env in:  SIVEP_LOGIN, SIVEP_SENHA, SOLICITACAO (the number from kestra_gerar).
Output:  writes the extracted <name>.dbf into the working dir (Kestra outputFiles: *.dbf).
"""

import asyncio
import os
import sys
from pathlib import Path

from playwright.async_api import async_playwright

import _sivep_kestra as k


async def main() -> int:
    numero = os.environ.get("SOLICITACAO", "").strip()
    if not numero:
        print("SOLICITACAO não definido.", file=sys.stderr)
        return 2

    pw = await async_playwright().start()
    browser = context = None
    try:
        browser, context, page = await k.open_logged_in(pw)
        dbf = await k.baixar_se_pronto(page, numero, dest_dir=Path("."))
        if dbf is None:
            print(
                f"Solicitação {numero} ainda não pronta — Kestra vai re-tentar.",
                file=sys.stderr,
            )
            return 1  # triggers the retry ladder
        print(f"DBF pronto: {dbf}", flush=True)
        return 0
    finally:
        if context:
            await context.close()
        if browser:
            await browser.close()
        await pw.stop()


sys.exit(asyncio.run(main()))
