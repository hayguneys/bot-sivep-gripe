"""Kestra task — Generate faixa etária (age group distribution) Excel files.

Calls sivep_core.run_faixa_etaria_sync() which generates Excel files for
both SG and SRAG_UTI ficha types, latest epidemiological week.

Outputs:
  - downloads/faixaetaria_US*.xls files (one per unit and ficha type)

Env in:
  SIVEP_LOGIN  -> SIVEP portal login
  SIVEP_SENHA  -> SIVEP portal password
"""

import os
import sys

# Add project root to path so we can import sivep_core
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sivep_core


def main() -> int:
    login = os.environ.get("SIVEP_LOGIN", "")
    senha = os.environ.get("SIVEP_SENHA", "")
    if not (login and senha):
        print("Erro: SIVEP_LOGIN e SIVEP_SENHA obrigatórios.", file=sys.stderr)
        return 1

    print("Iniciando geração de faixa etária (SG e SRAG)...")
    try:
        files = sivep_core.run_faixa_etaria_sync(
            headless=True,
            log=print,
        )
        if files:
            print(f"\n✓ {len(files)} arquivo(s) gerado(s):")
            for f in files:
                print(f"  - {f}")
            return 0
        else:
            print("Nenhum arquivo gerado.", file=sys.stderr)
            return 1
    except Exception as e:
        print(f"Erro na geração: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
