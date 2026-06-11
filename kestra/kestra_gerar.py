"""Kestra task 1 — request a latest-week export and emit the solicitacao number.

Env in:  SIVEP_LOGIN, SIVEP_SENHA, ANO (e.g. 2026), TIPO (default 3 = SRAG Hosp).
Output:  a Kestra output line  ::{"outputs":{"solicitacao":"<num>"}}::
"""

import asyncio
import os

from playwright.async_api import async_playwright

import _sivep_kestra as k


async def main():
    ano = os.environ.get("ANO") or __import__("datetime").date.today().strftime("%Y")
    tipo = os.environ.get("TIPO", "3")  # 3 = SRAG Hospitalizado, 1 = SG
    pw = await async_playwright().start()
    browser = context = None
    try:
        browser, context, page = await k.open_logged_in(pw)
        num = await k.gerar(page, ano=ano, tipo_val=tipo, somente_ultima_semana=True)
        # Kestra captures this exact format as outputs.<taskid>.vars.solicitacao
        print(f'::{{"outputs":{{"solicitacao":"{num}"}}}}::', flush=True)
    finally:
        if context:
            await context.close()
        if browser:
            await browser.close()
        await pw.stop()


asyncio.run(main())
