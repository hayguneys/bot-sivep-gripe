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
    """Directory for user data (downloads, .env, session, browsers, logs).

    - Running from source: the folder this file lives in.
    - Frozen (PyInstaller .exe): a stable per-user folder next to the .exe if writable,
      otherwise %LOCALAPPDATA%\\bot-sivep-gripe. Never the temp _MEIPASS extraction dir.
    """
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        # Prefer a 'data' folder beside the exe; fall back to LOCALAPPDATA if read-only.
        candidate = exe_dir / "bot-sivep-gripe-data"
        try:
            candidate.mkdir(exist_ok=True)
            test = candidate / ".write_test"
            test.write_text("ok", encoding="utf-8")
            test.unlink()
            return candidate
        except Exception:
            appdata = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "bot-sivep-gripe"
            appdata.mkdir(parents=True, exist_ok=True)
            return appdata
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


def _chromium_present(browsers_dir: Path) -> bool:
    """True only if a chromium-* build with a real chrome.exe/chrome binary exists."""
    for d in browsers_dir.glob("chromium-*"):
        if (d / "chrome-win64" / "chrome.exe").exists() or (
            d / "chrome-linux" / "chrome"
        ).exists():
            return True
    return False


def ensure_chromium(browsers_dir: Path, log=print) -> None:
    """Make sure Playwright's Chromium is available in `browsers_dir`.

    Points PLAYWRIGHT_BROWSERS_PATH at the folder and, if no usable Chromium build is
    found, downloads it. This is what lets the .exe fetch the browser on first run
    instead of bundling ~150MB into the executable.

    Crucially this must work when FROZEN (PyInstaller). A frozen exe cannot run
    `python -m playwright install` (sys.executable is the GUI exe, not Python), so we
    call Playwright's driver install routine directly via its Python API.
    """
    browsers_dir.mkdir(parents=True, exist_ok=True)
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers_dir)

    if _chromium_present(browsers_dir):
        return  # already installed

    log("Chromium não encontrado — baixando (primeira execução, requer internet)…")

    # Use Playwright's driver directly. compute_driver_executable() returns the path to
    # the bundled Node driver + its main.js, which works both from source and frozen.
    import subprocess
    from playwright._impl._driver import compute_driver_executable, get_driver_env

    driver = compute_driver_executable()
    # Newer Playwright returns a (node_exe, cli_js) tuple; older returns a single path.
    if isinstance(driver, (tuple, list)):
        cmd = [str(driver[0]), str(driver[1]), "install", "chromium"]
    else:
        cmd = [str(driver), "install", "chromium"]

    env = get_driver_env()
    env["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers_dir)
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if proc.returncode != 0 or not _chromium_present(browsers_dir):
        raise RuntimeError(
            "Falha ao baixar o Chromium:\n"
            + (proc.stderr or proc.stdout or "sem saída")[-1500:]
        )
    log("Chromium instalado com sucesso.")


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
    # Ensure Chromium is present (downloads on first run if missing).
    ensure_chromium(paths["browsers"], log=log)

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


# --------------------------------------------------------------------------- #
# Relatório: Distribuição dos vírus respiratórios por Faixa Etária.
# RELATÓRIOS -> EPIDEMIOLÓGICOS -> DISTRIBUIÇÃO DOS VÍRUS RESPIRATÓRIOS POR FAIXA ETÁRIA
# Exports the on-screen HTML table to Excel, looping over municipal units.
# --------------------------------------------------------------------------- #

# Unidades Sentinela: "US" number -> <select> value (confirmed live for this account).
# US 165 is intentionally absent (not selectable for this login).
UNIDADES_US = {
    "153": "628",
    "159": "1077",
    "163": "11768",
    "164": "562",
    "167": "1320",
    "169": "564",
}

# Tipo de ficha options on THIS report (different from the DBF export form).
FAIXA_TIPOS_FICHA = {"1": "SG", "2": "SRAG_UTI"}

# Custom age ranges to define (Definir Faixas Etárias): (inicial, final); "" = open-ended.
FAIXAS_ETARIAS = [("0", "1"), ("2", "4"), ("5", "14"), ("15", "49"), ("50", "64"), ("65", "")]


async def _faixa_open_form(page, log=print):
    """Navigate RELATÓRIOS -> EPIDEMIOLÓGICOS -> ...POR FAIXA ETÁRIA (hover menus, click)."""
    await page.goto(PRINCIPAL_URL, wait_until="networkidle")
    await _settle(page)
    await page.locator("a.sf-with-ul[alt*='RELAT']").first.hover()
    await page.wait_for_timeout(800)
    epi = page.locator("a.sf-with-ul[alt*='EPIDEMIOL']").first
    if await epi.count():
        await epi.hover()
        await page.wait_for_timeout(800)
    # The menu link's accent makes text matching brittle; match by JS regex.
    await page.evaluate(
        """() => {
            const a = [...document.querySelectorAll('a')]
                .find(e => /RESPIRAT.*FAIXA ET/i.test(e.textContent));
            if (a) a.click();
        }"""
    )
    await page.wait_for_load_state("networkidle")
    await page.wait_for_timeout(2500)
    await _settle(page)


def _current_epi_week(today=None) -> tuple[str, str]:
    """Return (ano, semana) for the current Brazilian epidemiological week (SE).

    SE follows the MMWR/CDC convention: weeks start on Sunday; SE 1 is the week
    containing the first Sunday-based week with >=4 days in January (i.e. the week
    containing Jan 4). Returns strings.
    """
    from datetime import date, timedelta

    d = today or date.today()
    # Sunday that starts d's epi week (weekday(): Mon=0..Sun=6 -> days since Sunday).
    week_start = d - timedelta(days=(d.weekday() + 1) % 7)
    # Epi year is the year of the Thursday in that week (mid-week rule).
    thursday = week_start + timedelta(days=4)
    epi_year = thursday.year
    # Week 1 = the epi week whose Sunday-start contains Jan 4 of the epi year.
    def week1(yr):
        j4 = date(yr, 1, 4)
        return j4 - timedelta(days=(j4.weekday() + 1) % 7)

    se = (week_start - week1(epi_year)).days // 7 + 1
    if se < 1:  # belongs to the last week of the previous epi year
        epi_year -= 1
        se = (week_start - week1(epi_year)).days // 7 + 1
    return str(epi_year), str(se)


async def _faixa_move_all(page, choices_name, values=None):
    """Select option(s) in a dual-list 'choices' select and click its Adicionar (>) button."""
    choices = page.locator(f"select[name='{choices_name}']")
    if values is None:
        values = await choices.locator("option").evaluate_all("els => els.map(o => o.value)")
    await choices.select_option(values)
    await page.evaluate(
        """(name) => {
            const sel = document.querySelector(`select[name='${name}']`);
            const scope = sel.closest('tr') || sel.parentElement.parentElement;
            for (const b of scope.querySelectorAll("button,input[type=button],a,img")) {
                const t = (b.textContent || b.value || b.alt || b.title || '').trim();
                if (t === '>' || /adicion/i.test(t)) { b.click(); return; }
            }
            const btns = scope.querySelectorAll("button,input[type=button]");
            if (btns.length) btns[0].click();
        }""",
        choices_name,
    )
    await page.wait_for_timeout(700)


async def _faixa_set_unidade(page, value):
    """Set the Chosen.js-backed unidade select by value and fire its change AJAX."""
    await page.evaluate(
        """(val) => {
            const s = document.querySelector('#unidadeSentinela');
            s.value = val;
            s.dispatchEvent(new Event('change', {bubbles:true}));
            if (window.jQuery) jQuery(s).trigger('chosen:updated').trigger('change');
        }""",
        value,
    )


async def _faixa_definir_faixas(page, faixas, log=print):
    """Check 'Definir Faixas Etárias' (Wicket AJAX) and add each custom age range."""
    # Target by stable NAME — the id (e.g. id1e) is dynamic and changes per render.
    # The checkbox fires a Wicket AJAX that reveals campoInicial/Final. Click and wait
    # for that reveal; retry the click once if the fields don't appear (AJAX race).
    field = "[name='faixasEtarias:campoInicial']"
    for attempt in range(3):
        if await page.locator(field).is_visible():
            break
        # Use a native JS click — Playwright's .click() gets swallowed by the Wicket
        # AJAX re-render of the checkbox, so the reveal never fires.
        await page.evaluate(
            "() => document.querySelector"
            "(\"[name='faixasEtarias:checkFaixasEtarias']\").click()"
        )
        await page.wait_for_timeout(2800)
        await _settle(page)
        log(f"Faixas: aguardando campos (tentativa {attempt + 1}).")
    await page.wait_for_selector(field, state="visible", timeout=6000)
    for ini, fim in faixas:
        await page.fill("[name='faixasEtarias:campoInicial']", ini)
        await page.fill("[name='faixasEtarias:campoFinal']", fim)
        # Click the faixas add control: button.botaoAdicionar, whose onclick is
        # Wicket.FaixasEtarias.add('campoInicial','campoFinal',...).
        await page.click("button.botaoAdicionar")
        await page.wait_for_timeout(700)
    n = await page.locator("[name='faixasEtarias:listSelection'] option").count()
    log(f"Faixas etárias definidas: {n}")


async def run_faixa_etaria(
    *,
    units=None,
    headless: bool = True,
    slow_mo_ms: int = 0,
    log=print,
    base_dir: Path | None = None,
    should_cancel=None,
) -> list[Path]:
    """Export the 'Distribuição dos vírus por faixa etária' table to Excel.

    For each municipal unit and each ficha type (SG, SRAG UTI), fills the form for the
    latest epidemiological week, selects all vírus + IFI/PCR + the custom age ranges,
    clicks Consultar, exports the table to Excel, then Voltar. Returns saved .xls paths.
    """
    units = units or list(UNIDADES_US.keys())
    paths = _paths(base_dir)
    ensure_chromium(paths["browsers"], log=log)
    login, senha = load_credentials(paths["project"])
    if not (login and senha):
        raise RuntimeError("Missing credentials (set them in the GUI / .env).")

    def _cancelled():
        return bool(should_cancel and should_cancel())

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    saved: list[Path] = []

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=headless, slow_mo=slow_mo_ms)
    context = await browser.new_context(accept_downloads=True)
    page = await context.new_page()
    try:
        # Login.
        await page.goto(LOGIN_URL, wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)
        if "login.html" in page.url:
            await page.fill("input[name='email']", login)
            await page.fill("input[name='senha']", senha)
            await page.click("input[type='submit'][name='ENTRAR']")
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(2000)
            if "login.html" in page.url:
                raise RuntimeError("Login falhou — confira as credenciais.")
        await _settle(page)
        log(f"Login OK -> {page.url}")

        for us in units:
            if _cancelled():
                log("Cancelado pelo usuário.")
                break
            value = UNIDADES_US.get(us)
            if not value:
                log(f"US {us} não está na lista desta conta — pulando.")
                continue

            for tipo_val, tipo_nome in FAIXA_TIPOS_FICHA.items():
                if _cancelled():
                    break
                log(f"===== US {us} | {tipo_nome} =====")
                await _faixa_open_form(page, log)

                # Select the unidade first (its AJAX may populate the period defaults).
                await _faixa_set_unidade(page, value)
                await page.wait_for_timeout(1500)
                await _settle(page)

                # Period = current epidemiological week (Ano is required; fill it BEFORE
                # the faixas checkbox, whose Wicket AJAX validates the form).
                ano, sem = _current_epi_week()
                await page.fill("[name='periodo:ano']", ano)
                await page.fill("[name='periodo:semanaInicial']", sem)
                await page.fill("[name='periodo:semanaFinal']", sem)
                # Nudge Wicket to register the values (blur via Tab).
                await page.locator("[name='periodo:semanaFinal']").press("Tab")
                await page.wait_for_timeout(800)
                log(f"Período: ano {ano}, semana epidemiológica {sem}")

                await _faixa_move_all(page, "listaContainerTipoFicha:choices", [tipo_val])
                await _faixa_move_all(page, "listaContainerTipoExame:choices", ["1", "2"])
                await _faixa_move_all(page, "listaContainerTipoVirusRespiratorio:choices", None)
                await _faixa_definir_faixas(page, FAIXAS_ETARIAS, log)

                await page.click("input#consultar")
                await page.wait_for_load_state("networkidle")
                await page.wait_for_timeout(3000)
                await _settle(page)

                btn = page.locator("input#exportarExcel, input[name='exportarExcel']").first
                if not await btn.count():
                    log(f"US {us} {tipo_nome}: sem resultado / botão Excel ausente — pulando.")
                    continue
                async with page.expect_download(timeout=120000) as info:
                    await btn.click()
                dl = await info.value
                target = paths["downloads"] / (
                    f"faixaetaria_US{us}_{tipo_nome}_{ano}_se{sem}_{run_id}_{dl.suggested_filename}"
                )
                await dl.save_as(str(target))
                saved.append(target)
                log(f"Excel salvo -> {target}")

                # Voltar to the form for the next iteration.
                voltar = page.locator("input#voltar, input[name='voltar']").first
                if await voltar.count():
                    await voltar.click()
                    await page.wait_for_load_state("networkidle")
                    await _settle(page)
    finally:
        await context.close()
        await browser.close()
        await pw.stop()

    log("===== FAIXA ETÁRIA: concluído =====")
    for f in saved:
        log(f"  salvo: {f}")
    return saved


def run_faixa_etaria_sync(*args, **kwargs) -> list[Path]:
    """Blocking wrapper around :func:`run_faixa_etaria` (UI thread / CLI)."""
    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    return asyncio.run(run_faixa_etaria(*args, **kwargs))


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
