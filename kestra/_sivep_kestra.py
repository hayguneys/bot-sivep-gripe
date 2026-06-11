"""Shared helpers for the Kestra SIVEP tasks.

Built entirely on top of the existing ``sivep_core`` low-level helpers — it does NOT
modify the main app. The Kestra scripts split the monolithic ``run_exports`` into the
two stages the workflow needs:

  * gerar  -> log in, request a latest-week export, return the new solicitacao number
  * baixar -> log in, check ONE TIME if a given solicitacao is ready; download if so

Credentials come from env (SIVEP_LOGIN / SIVEP_SENHA), set by Kestra secrets.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Make sivep_core importable whether this runs from the kestra/ folder or the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import sivep_core as core  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402


def log(msg: str) -> None:
    print(msg, flush=True)


def _credentials() -> tuple[str, str]:
    login = os.environ.get("SIVEP_LOGIN", "").strip()
    senha = os.environ.get("SIVEP_SENHA", "")
    if not (login and senha):
        raise RuntimeError("SIVEP_LOGIN / SIVEP_SENHA não definidos (use Kestra secrets).")
    return login, senha


async def open_logged_in(pw):
    """Launch headless Chromium and log in fresh (stateless — no saved session)."""
    login, senha = _credentials()
    # In a Playwright image Chromium is already present; ensure_chromium is a no-op then.
    core.ensure_chromium(Path(os.environ.get("BROWSERS_DIR", "/tmp/pw-browsers")), log=log)

    browser = await pw.chromium.launch(headless=True)
    context = await browser.new_context(accept_downloads=True)
    page = await context.new_page()

    await page.goto(core.LOGIN_URL, wait_until="domcontentloaded")
    await page.wait_for_timeout(1500)
    if "login.html" in page.url:
        await page.fill("input[name='email']", login)
        await page.fill("input[name='senha']", senha)
        await page.click("input[type='submit'][name='ENTRAR']")
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(2000)
        if "login.html" in page.url:
            raise RuntimeError("Login falhou — confira as credenciais.")
    await core._settle(page)
    log(f"Login OK -> {page.url}")
    return browser, context, page


async def gerar(page, ano: str, tipo_val: str, somente_ultima_semana: bool = True) -> str:
    """Request one export and return the new Numero de Solicitacao (string).

    Mirrors the form-fill sequence inside core.run_exports, but stops right after
    'Gerar Arquivo' and detects the newly-created solicitacao number.
    """
    await page.goto(core.PRINCIPAL_URL, wait_until="networkidle")
    await core._settle(page)
    await core._open_export_item(page, "CONSULTAR EXPORTAÇÕES DBF")
    before = await core._listar_solicitacoes(page)
    log(f"Solicitacoes antes: {sorted(before)}")

    await core._open_export_item(page, "REGISTROS INDIVIDUAIS")
    await page.select_option("select#tipoFicha", tipo_val)
    await page.wait_for_timeout(2000)
    await core._settle(page)

    await page.check("[name='periodo:anoEpidemiologico']")
    await page.wait_for_timeout(500)
    await page.fill("[name='periodo:anoAnoEpidemiologico']", ano)
    await page.locator("[name='periodo:anoAnoEpidemiologico']").press("Tab")
    await page.wait_for_timeout(1500)
    sf = await page.input_value("[name='periodo:semanaFinal']")
    if somente_ultima_semana and sf:
        await page.fill("[name='periodo:semanaInicial']", sf)
        log(f"Somente última semana: {sf}")

    await page.check("[name='chkExportarDadosPaciente']")
    await page.click("[name='gerarDbf']")
    await page.wait_for_load_state("networkidle")
    await page.wait_for_timeout(2000)
    await core._settle(page)
    log("Gerar Arquivo clicado — export na fila.")

    # Find the new solicitacao number that appeared.
    await core._open_export_item(page, "CONSULTAR EXPORTAÇÕES DBF")
    now = await core._listar_solicitacoes(page)
    created = sorted(now - before, key=int, reverse=True)
    if not created:
        # It can take a few seconds to list; refresh once.
        await core._refresh_dbf_page(page, log)
        now = await core._listar_solicitacoes(page)
        created = sorted(now - before, key=int, reverse=True)
    if not created:
        raise RuntimeError("Não foi possível identificar a nova solicitação.")
    num = created[0]
    log(f"Nova solicitação: {num}")
    return num


async def baixar_se_pronto(page, numero: str, dest_dir: Path) -> Path | None:
    """Check ONCE if `numero` is concluded. If so, download + extract; return .dbf path.
    Returns None if not ready yet (caller / Kestra decides to retry)."""
    await page.goto(core.PRINCIPAL_URL, wait_until="networkidle")
    await core._settle(page)
    await core._open_export_item(page, "CONSULTAR EXPORTAÇÕES DBF")
    await core._refresh_dbf_page(page, log)  # real Wicket refresh so status is current

    row = await core._row_for_num(page, numero)
    if row is None:
        log(f"Solicitação {numero} ainda não listada.")
        return None
    status = (await row.inner_text()).lower()
    if "conclu" not in status:
        log(f"Solicitação {numero} ainda processando.")
        return None

    dl_link = row.locator("a:has-text('Download')").first
    if not await dl_link.count():
        log(f"Solicitação {numero} concluída mas sem link de download.")
        return None

    dest_dir.mkdir(parents=True, exist_ok=True)
    async with page.expect_download(timeout=120000) as info:
        await dl_link.click()
    dl = await info.value
    zip_path = dest_dir / f"{numero}_{dl.suggested_filename}"
    await dl.save_as(str(zip_path))
    log(f"Baixado zip -> {zip_path}")

    extracted = core._extract_dbf(zip_path, dest_dir, log=log)
    if extracted:
        return extracted[0]
    # If it wasn't a zip (unexpected), return the raw file.
    return zip_path
