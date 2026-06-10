"""Core SIVEP-Gripe automation, extracted from the notebook so it can also be driven
from the Qt UI (sivep_ui.py) or any plain script.

The public entry point is ``run_exports(...)`` (async) and its sync wrapper
``run_exports_sync(...)``. Pass a ``log`` callback to receive progress messages.

This module is OS-agnostic: on Windows it selects the Proactor event loop (needed by
Playwright's subprocess) inside the sync wrapper; on Linux/macOS the default loop works.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import async_playwright

LOGIN_URL = "https://sivepgripe.saude.gov.br/sivepgripe/login.html?1"
PRINCIPAL_URL = "https://sivepgripe.saude.gov.br/sivepgripe/visao/pages/principal.html?1"

# select#tipoFicha values -> human label used in output filenames.
# Only SRAG Hospitalizado (3) and SG (1) are allowed; SRAG UTI (2) is intentionally
# excluded everywhere (UI, CLI, programmatic). ALLOWED_TIPOS is the single source of truth.
ALLOWED_TIPOS = {
    "3": "SRAG_Hospitalizado",
    "1": "SG",
}
TIPO_FICHA_LABELS = ALLOWED_TIPOS


def project_dir() -> Path:
    """Project root = the folder this file lives in."""
    return Path(__file__).resolve().parent


def env_path(base: Path | None = None) -> Path:
    """Path to the local .env that holds the user's credentials (git-ignored)."""
    return (base or project_dir()) / ".env"


def load_credentials(base: Path | None = None) -> tuple[str, str]:
    """Return (login, senha) from .env, or ('', '') if not set yet."""
    load_dotenv(env_path(base), override=True)
    return os.environ.get("SIVEP_LOGIN", ""), os.environ.get("SIVEP_SENHA", "")


def save_credentials(login: str, senha: str, base: Path | None = None) -> None:
    """Persist credentials to the git-ignored .env so they are reused on later runs."""
    p = env_path(base)
    p.write_text(f"SIVEP_LOGIN={login}\nSIVEP_SENHA={senha}\n", encoding="utf-8")
    os.environ["SIVEP_LOGIN"] = login
    os.environ["SIVEP_SENHA"] = senha


def _paths(base: Path | None = None) -> dict[str, Path]:
    base = base or project_dir()
    paths = {
        "project": base,
        "browsers": base / ".playwright-browsers",
        "downloads": base / "downloads",
        "state": base / "state",
        "logs": base / "logs",
    }
    paths["state_file"] = paths["state"] / "storage_state.json"
    for key in ("downloads", "state", "logs"):
        paths[key].mkdir(exist_ok=True)
    return paths


# --------------------------------------------------------------------------- #
# Wicket UI helpers (same quirks the notebook documents).
# --------------------------------------------------------------------------- #

async def _settle(page):
    """Dismiss the startup 'Alerta' modal, then wait for overlay + spinner to vanish."""
    ok = page.locator(".ui-dialog button:has-text('Ok'), button:has-text('Ok')").first
    if await ok.count() and await ok.is_visible():
        await ok.click()
        await page.wait_for_timeout(600)
    for sel in (".ui-widget-overlay", "#div-carregando"):
        try:
            await page.wait_for_selector(sel, state="hidden", timeout=8000)
        except Exception:
            pass


async def _open_export_item(page, label):
    """Hover the EXPORTACAO menu and click a visible submenu link by accessible name."""
    await _settle(page)
    await page.locator("a.sf-with-ul[alt*='EXPORTA']").first.hover()
    await page.wait_for_timeout(900)
    link = page.get_by_role("link", name=label)
    for i in range(await link.count()):
        if await link.nth(i).is_visible():
            await link.nth(i).click()
            break
    await page.wait_for_load_state("networkidle")
    await page.wait_for_timeout(1500)
    await _settle(page)


async def _listar_solicitacoes(page):
    """Return the set of 'Numero de Solicitacao' values currently in the DBF table."""
    nums = set()
    rows = page.locator("table tr")
    for r in range(await rows.count()):
        first_cell = rows.nth(r).locator("td").first
        if await first_cell.count():
            txt = (await first_cell.inner_text()).strip()
            if txt.isdigit():
                nums.add(txt)
    return nums


async def _row_for_num(page, num):
    """Return the <tr> locator whose first cell is exactly `num`, or None.

    Uses the same row iteration as _listar_solicitacoes so detection and status
    reading stay consistent (avoids `:text-is` mismatches and re-render races).
    """
    rows = page.locator("table tr")
    for r in range(await rows.count()):
        row = rows.nth(r)
        first_cell = row.locator("td").first
        if await first_cell.count():
            txt = (await first_cell.inner_text()).strip()
            if txt == num:
                return row
    return None


async def _newest_concluded(page):
    """Return (num, row) for the highest-numbered 'Processamento Concluido' export
    that has a working Download link, or (None, None). Solicitation numbers increase
    monotonically, so the highest number is the newest export."""
    best_num = None
    best_row = None
    rows = page.locator("table tr")
    for r in range(await rows.count()):
        row = rows.nth(r)
        first_cell = row.locator("td").first
        if not await first_cell.count():
            continue
        num = (await first_cell.inner_text()).strip()
        if not num.isdigit():
            continue
        try:
            status = (await row.inner_text()).lower()
        except Exception:
            continue
        if "conclu" not in status:
            continue
        if not await row.locator("a:has-text('Download')").first.count():
            continue
        if best_num is None or int(num) > int(best_num):
            best_num, best_row = num, row
    return best_num, best_row


async def _refresh_dbf_page(page, log=print):
    """Refresh the CONSULTAR EXPORTACOES DBF table so newly-finished exports appear.

    The page's 'Atualizar' control is a Wicket form submit (input#atualizar, value
    'Atualizar' -- mixed case, displayed uppercase via CSS). Clicking it re-runs the
    server-side query, unlike page.reload() which re-POSTs the stale page version and
    does NOT pick up status changes. If the button is missing/disabled, fall back to
    a full re-navigation via the menu (equivalent to leaving and re-entering the page).
    """
    btn = page.locator(
        "input#atualizar, "
        "input[name='atualizar'], "
        "input[type='submit'][value='Atualizar' i], "
        "button:has-text('Atualizar')"
    ).first
    if await btn.count() and await btn.is_enabled():
        await btn.click()
        await page.wait_for_load_state("networkidle")
        await _settle(page)
        return
    # Fallback: leave to principal and re-open the page from the menu (forces a fresh query).
    log("Atualizar button not found -> re-navigating to refresh the DBF list")
    await page.goto(PRINCIPAL_URL, wait_until="networkidle")
    await _settle(page)
    await _open_export_item(page, "CONSULTAR EXPORTAÇÕES DBF")


def _extract_dbf(zip_path, dest_dir, log=print):
    """If the download is a .zip, extract its .dbf members into dest_dir.
    Returns the list of extracted .dbf paths (empty if the file wasn't a zip)."""
    import zipfile

    if not str(zip_path).lower().endswith(".zip") or not zipfile.is_zipfile(zip_path):
        return []
    extracted = []
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            if name.lower().endswith(".dbf"):
                out = Path(dest_dir) / Path(name).name
                with zf.open(name) as src, open(out, "wb") as dst:
                    dst.write(src.read())
                extracted.append(out)
                log(f"Extracted -> {out}")
    return extracted


# --------------------------------------------------------------------------- #
# Main flow.
# --------------------------------------------------------------------------- #

async def run_exports(
    ano: str,
    tipos_ficha: dict[str, str],
    *,
    headless: bool = True,
    exportar_dados_paciente: bool = True,
    somente_ultima_semana: bool = False,
    slow_mo_ms: int = 0,
    processing_timeout_s: int = 3600,
    log=print,
    base_dir: Path | None = None,
    should_cancel=None,
) -> list[Path]:
    """Generate + download one DBF per ficha type. Returns the saved file paths.

    ``log`` is called with progress strings. ``should_cancel`` (optional) is a
    zero-arg callable returning True to abort between steps (used by the UI's Stop).
    If ``somente_ultima_semana`` is True, only the most recent epidemiological week
    is exported (Semana Inicial = Semana Final) instead of the full year.
    """
    # Enforce the allow-list: only SRAG Hospitalizado (3) and SG (1) may be downloaded.
    disallowed = [t for t in tipos_ficha if t not in ALLOWED_TIPOS]
    if disallowed:
        raise ValueError(
            f"tipoFicha {disallowed} not allowed. Only {sorted(ALLOWED_TIPOS)} "
            f"(SRAG Hospitalizado=3, SG=1) may be exported."
        )

    paths = _paths(base_dir)
    if paths["browsers"].exists():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(paths["browsers"])

    login, senha = load_credentials(paths["project"])
    if not (login and senha):
        raise RuntimeError(
            "Missing credentials. Enter them in the GUI (Credenciais) or run the CLI, "
            "which will prompt and save them to .env."
        )

    def _cancelled() -> bool:
        return bool(should_cancel and should_cancel())

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    state_file = paths["state_file"]
    downloaded: list[Path] = []

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=headless, slow_mo=slow_mo_ms)
    ctx_kwargs = {"accept_downloads": True}
    if state_file.exists():
        ctx_kwargs["storage_state"] = str(state_file)
        log("Reusing saved session from state/storage_state.json")
    context = await browser.new_context(**ctx_kwargs)
    page = await context.new_page()
    log(f"Browser launched (headless={headless})")

    try:
        # ---- Login (or reuse session) ----
        await page.goto(LOGIN_URL, wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)
        if "login.html" in page.url:
            log("Login form present -> filling credentials")
            await page.fill("input[name='email']", login)
            await page.fill("input[name='senha']", senha)
            await page.click("input[type='submit'][name='ENTRAR']")
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(2000)
            if "login.html" in page.url:
                raise RuntimeError("Login failed -> still on login page. Check credentials.")
            log(f"Login OK -> {page.url}")
            await context.storage_state(path=str(state_file))
            log("Session saved to state/storage_state.json")
        else:
            log("Already authenticated via saved session")
        await _settle(page)

        # ---- Per ficha type ----
        for tipo_val, tipo_nome in tipos_ficha.items():
            if _cancelled():
                log("Cancelled by user.")
                break
            log(f"========== {tipo_nome} (tipoFicha={tipo_val}) ==========")

            await page.goto(PRINCIPAL_URL, wait_until="networkidle")
            await _settle(page)
            await _open_export_item(page, "CONSULTAR EXPORTAÇÕES DBF")
            before = await _listar_solicitacoes(page)
            log(f"Existing solicitacoes before: {sorted(before)}")

            await _open_export_item(page, "REGISTROS INDIVIDUAIS")
            await page.select_option("select#tipoFicha", tipo_val)
            await page.wait_for_timeout(2000)
            await _settle(page)

            await page.check("[name='periodo:anoEpidemiologico']")
            await page.wait_for_timeout(500)
            await page.fill("[name='periodo:anoAnoEpidemiologico']", ano)
            await page.locator("[name='periodo:anoAnoEpidemiologico']").press("Tab")
            await page.wait_for_timeout(1500)
            si = await page.input_value("[name='periodo:semanaInicial']")
            sf = await page.input_value("[name='periodo:semanaFinal']")

            if somente_ultima_semana:
                # Use the portal's own 'last available week' (the auto-filled Semana Final)
                # and set Semana Inicial = Semana Final, so only that week is exported.
                if sf:
                    await page.fill("[name='periodo:semanaInicial']", sf)
                    si = sf
                log(f"Year {ano} -> ONLY latest week: Semana Inicial={si}, Semana Final={sf}")
            else:
                log(f"Year {ano} -> Semana Inicial={si}, Semana Final={sf}")

            if exportar_dados_paciente:
                await page.check("[name='chkExportarDadosPaciente']")

            await page.click("[name='gerarDbf']")
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(2000)
            await _settle(page)
            log("Gerar Arquivo clicked -> export queued")

            # Poll the DBF page until OUR new request is concluded. As a fallback (so a
            # ready export is never stranded), if our request hasn't appeared/finished but
            # another export is already concluded, download the newest concluded one.
            await _open_export_item(page, "CONSULTAR EXPORTAÇÕES DBF")
            deadline = asyncio.get_event_loop().time() + processing_timeout_s
            dl_num = None
            dl_row = None
            while asyncio.get_event_loop().time() < deadline:
                if _cancelled():
                    log("Cancelled by user.")
                    break
                now = await _listar_solicitacoes(page)
                created = sorted(now - before, key=int, reverse=True)
                if created:
                    new_num = created[0]
                    row = await _row_for_num(page, new_num)
                    status = ""
                    if row is not None:
                        try:
                            status = (await row.inner_text()).lower()
                        except Exception:
                            status = ""
                    if "conclu" in status and await row.locator(
                        "a:has-text('Download')"
                    ).first.count():
                        dl_num, dl_row = new_num, row
                        log(f"Our solicitacao {new_num} concluded -> downloading")
                        break
                    log(f"Solicitacao {new_num} not ready yet -> ATUALIZAR in 15s")
                else:
                    log("New solicitacao not listed yet -> ATUALIZAR in 15s")
                await page.wait_for_timeout(15000)
                await _refresh_dbf_page(page, log)

            # Fallback: our request never completed in time, but grab the newest
            # already-concluded export so a ready file is not left behind.
            if dl_row is None and not _cancelled():
                fb_num, fb_row = await _newest_concluded(page)
                if fb_row is not None:
                    dl_num, dl_row = fb_num, fb_row
                    log(
                        f"Our request not ready; downloading newest concluded "
                        f"export instead -> {fb_num}"
                    )

            if dl_row is not None:
                async with page.expect_download(timeout=120000) as dl_info:
                    await dl_row.locator("a:has-text('Download')").first.click()
                dl = await dl_info.value
                # Prefix with the portal's Numero de Solicitacao so each file is traceable
                # back to its request on CONSULTAR EXPORTACOES DBF.
                target = (
                    paths["downloads"]
                    / f"{dl_num}_{tipo_nome}_{ano}_{run_id}_{dl.suggested_filename}"
                )
                await dl.save_as(str(target))
                downloaded.append(target)
                log(f"Saved -> {target}")
                # SIVEP serves a .zip wrapping the .dbf; extract it for the DBF viewer.
                _extract_dbf(target, paths["downloads"], log=log)
            else:
                log(f"TIMEOUT/Cancel: {tipo_nome} export did not finish.")

        await context.storage_state(path=str(state_file))
    finally:
        await context.close()
        await browser.close()
        await pw.stop()

    log("========== DONE ==========")
    for f in downloaded:
        log(f"  downloaded: {f}")
    return downloaded


def run_exports_sync(*args, **kwargs) -> list[Path]:
    """Blocking wrapper around :func:`run_exports` for non-async callers (UI thread/CLI).

    Selects the Proactor event loop on Windows so Playwright can spawn its subprocess.
    """
    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    return asyncio.run(run_exports(*args, **kwargs))


if __name__ == "__main__":
    # Minimal CLI: python sivep_core.py 2024 3,1
    import argparse

    parser = argparse.ArgumentParser(description="SIVEP-Gripe DBF exporter")
    parser.add_argument("ano", help="Epidemiological year, e.g. 2024")
    parser.add_argument(
        "tipos",
        nargs="?",
        default="3,1",
        help="Comma-separated tipoFicha values (1=SG, 2=SRAG UTI, 3=SRAG Hosp). Default: 3,1",
    )
    parser.add_argument("--headed", action="store_true", help="Show the browser window")
    parser.add_argument(
        "--ultima-semana",
        action="store_true",
        help="Export only the most recent epidemiological week (not the whole year)",
    )
    args = parser.parse_args()

    # Prompt for credentials on first use and save them to .env for next time.
    _login, _senha = load_credentials()
    if not (_login and _senha):
        import getpass

        print("Credenciais SIVEP nao encontradas. Informe (serao salvas em .env):")
        _login = input("  Login (e-mail): ").strip()
        _senha = getpass.getpass("  Senha: ").strip()
        save_credentials(_login, _senha)
        print("  Credenciais salvas em .env")

    tipos = {v: TIPO_FICHA_LABELS.get(v, v) for v in args.tipos.split(",") if v}
    run_exports_sync(
        args.ano,
        tipos,
        headless=not args.headed,
        somente_ultima_semana=args.ultima_semana,
        slow_mo_ms=0,
    )
