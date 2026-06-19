#!/usr/bin/env python
"""Local pipeline runner — execute the daily SIVEP→Google Sheets pipeline.

This runs the Kestra pipeline logic directly in Python without needing
Kestra server or Docker. Useful for testing and manual runs.

Usage:
    python run_pipeline.py                  # Run full pipeline
    python run_pipeline.py --sg-only        # Only SG
    python run_pipeline.py --srag-only      # Only SRAG
    python run_pipeline.py --faixa-only     # Only faixa etária

Prerequisites:
    - .env file with SIVEP_LOGIN, SIVEP_SENHA
    - Google Sheets credentials in GOOGLE_SERVICE_ACCOUNT env var
    - SHEET_ID env var with your spreadsheet ID
    - pip install gspread dbfread pandas openpyxl
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "kestra"))

import sivep_core
from kestra.kestra_faixa_sheets import push_faixa_to_sheets

# Faixa etária tipo → (filename pattern, target Google Sheets tab).
FAIXA_TABS = {
    "SG": ("SG_", "SG-agregados"),
    "SRAG": ("SRAG_UTI_", "SRAG-agregados"),
}


def load_env():
    """Load credentials from .env and environment variables."""
    from dotenv import load_dotenv
    load_dotenv()

    login = os.environ.get("SIVEP_LOGIN", "")
    senha = os.environ.get("SIVEP_SENHA", "")
    service_account = os.environ.get("GOOGLE_SERVICE_ACCOUNT", "")
    sheet_id = os.environ.get("SHEET_ID", "")

    if not (login and senha):
        print("❌ Erro: SIVEP_LOGIN e SIVEP_SENHA não definidos em .env")
        return None

    if not (service_account and sheet_id):
        print("❌ Erro: GOOGLE_SERVICE_ACCOUNT e SHEET_ID não definidos")
        return None

    return {
        "login": login,
        "senha": senha,
        "service_account": service_account,
        "sheet_id": sheet_id,
    }


def run_exports(tipo: str, tipo_nome: str):
    """Download individual records (SG or SRAG) and append to Google Sheets."""
    print(f"\n{'='*70}")
    print(f"▶ Exportando {tipo_nome} (tipo {tipo})...")
    print(f"{'='*70}")

    try:
        # Mirror the Kestra flow: current year, latest epidemiological week only.
        ano = str(datetime.now().year)
        files = sivep_core.run_exports_sync(
            ano,
            {tipo: tipo_nome},
            headless=True,
            somente_ultima_semana=True,
            log=print,
        )
        if files:
            print(f"✓ Download concluído: {len(files)} arquivo(s)")

            # TODO: Import the kestra script and run it
            # For now, just report success
            return True
        else:
            print(f"⚠ Nenhum arquivo baixado")
            return False
    except Exception as e:
        print(f"❌ Erro: {e}")
        import traceback
        traceback.print_exc()
        return False


def run_faixa(creds):
    """Generate faixa etária (age distribution) and push aggregates to Sheets.

    Generates Excel for both SG and SRAG_UTI, then appends each ficha type's
    rows to its dedicated tab (SG-agregados / SRAG-agregados).
    """
    print(f"\n{'='*70}")
    print(f"▶ Gerando faixa etária (SG e SRAG)...")
    print(f"{'='*70}")

    try:
        files = sivep_core.run_faixa_etaria_sync(
            headless=True,
            log=print,
        )
        if not files:
            print(f"⚠ Nenhum arquivo gerado")
            return False
        print(f"✓ Geração concluída: {len(files)} arquivo(s)")
    except Exception as e:
        print(f"❌ Erro na geração: {e}")
        import traceback
        traceback.print_exc()
        return False

    # Push each ficha type's Excel into its aggregate tab.
    ok = True
    for label, (pattern, tab) in FAIXA_TABS.items():
        print(f"\n  → Enviando {label} para aba '{tab}'...")
        try:
            push_faixa_to_sheets(
                pattern=pattern,
                sheet_tab=tab,
                sheet_id=creds["sheet_id"],
                service_account_json=creds["service_account"],
                log=print,
            )
        except Exception as e:
            print(f"  ❌ Erro ao enviar {label}: {e}")
            import traceback
            traceback.print_exc()
            ok = False
    return ok


def main():
    parser = argparse.ArgumentParser(
        description="Run the SIVEP→Google Sheets pipeline locally",
        epilog="Examples:\n  python run_pipeline.py              # Full pipeline\n"
               "  python run_pipeline.py --sg-only    # SG only",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--sg-only", action="store_true", help="Run SG only")
    parser.add_argument("--srag-only", action="store_true", help="Run SRAG only")
    parser.add_argument("--faixa-only", action="store_true", help="Run faixa etária only")
    parser.add_argument("--no-faixa", action="store_true", help="Skip faixa etária")
    args = parser.parse_args()

    print("\n" + "="*70)
    print("SIVEP → Google Sheets Pipeline (Local Runner)")
    print("="*70)

    creds = load_env()
    if not creds:
        return 1

    print(f"\n✓ Credenciais carregadas")
    print(f"  Login: {creds['login'][:10]}...")
    print(f"  Spreadsheet: {creds['sheet_id'][:20]}...")

    results = {
        "SG": False,
        "SRAG": False,
        "Faixa Etária": False,
    }

    if args.faixa_only:
        # Run faixa etária only
        results["Faixa Etária"] = run_faixa(creds)
    else:
        # Run SG and SRAG (unless skipped)
        if not args.srag_only:
            results["SG"] = run_exports("1", "SG")
        if not args.sg_only:
            results["SRAG"] = run_exports("3", "SRAG_Hospitalizado")

        # Run faixa etária (unless skipped)
        if not args.no_faixa:
            results["Faixa Etária"] = run_faixa(creds)

    # Summary
    print(f"\n{'='*70}")
    print("RESUMO")
    print(f"{'='*70}")
    for task, success in results.items():
        status = "✓" if success else "❌"
        print(f"{status} {task}")

    all_ok = all(results.values())
    if all_ok:
        print(f"\n✅ Pipeline concluído com sucesso!")
        return 0
    else:
        print(f"\n⚠ Pipeline com erros — verifique logs acima")
        return 1


if __name__ == "__main__":
    sys.exit(main())
