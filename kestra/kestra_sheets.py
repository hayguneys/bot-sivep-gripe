"""Kestra task 3 — read the .dbf, dedup against the sheet, append new rows.

Dedup key: NU_NOTIFIC (the SIVEP notification number, unique per record). Rows whose
NU_NOTIFIC is already in column A of the target tab are skipped, so daily runs with
overlapping week windows stay idempotent.

Env in:
  GOOGLE_SERVICE_ACCOUNT  -> service-account JSON (string)
  SHEET_ID                -> spreadsheet id (from its URL)
  SHEET_TAB               -> worksheet/tab name (default 'dados')
  DBF_FILE                -> path to the .dbf produced by kestra_baixar
"""

import json
import os
import sys
from pathlib import Path

import gspread
import pandas as pd
from dbfread import DBF

DEDUP_COL = "NU_NOTIFIC"


def main() -> int:
    # DBF_FILE may be unset (files arrive via the task's inputFiles into the
    # working dir) — fall back to the newest *.dbf found there.
    dbf_file = os.environ.get("DBF_FILE", "")
    if not (dbf_file and os.path.isfile(dbf_file)):
        candidates = sorted(Path(".").rglob("*.dbf"), key=lambda p: p.stat().st_mtime)
        if not candidates:
            print("Nenhum .dbf encontrado no diretório de trabalho.", file=sys.stderr)
            return 2
        dbf_file = str(candidates[-1])
    print(f"Lendo {dbf_file}")
    df = pd.DataFrame(iter(DBF(dbf_file, encoding="latin-1", char_decode_errors="replace")))
    if df.empty:
        print("DBF sem registros — nada a enviar.")
        return 0
    if DEDUP_COL not in df.columns:
        print(f"Coluna {DEDUP_COL} ausente no DBF.", file=sys.stderr)
        return 2

    creds = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT"])
    gc = gspread.service_account_from_dict(creds)
    sh = gc.open_by_key(os.environ["SHEET_ID"])
    tab = os.environ.get("SHEET_TAB", "dados")
    try:
        ws = sh.worksheet(tab)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=tab, rows=1, cols=len(df.columns))

    existing = ws.col_values(1)  # column A, including header if present
    header_present = existing[:1] == [DEDUP_COL]
    existing_keys = set(existing[1:]) if header_present else set()

    novos = df[~df[DEDUP_COL].astype(str).isin(existing_keys)]

    # Write header on first use.
    if not header_present:
        ws.update([df.columns.astype(str).tolist()], "A1")

    if len(novos):
        ws.append_rows(
            novos.astype(str).values.tolist(),
            value_input_option="RAW",
        )
    print(f"Anexadas {len(novos)} novas linhas (de {len(df)} no DBF).")
    return 0


sys.exit(main())
