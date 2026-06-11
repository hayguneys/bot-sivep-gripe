"""Qt6 (PySide6) UI for the SIVEP-Gripe downloader.

Two tabs:
  * Runner  -> configure year / ficha types, run the automation in a background
               thread, watch the live log.
  * DBF Viewer -> list files in downloads/, open a .dbf and browse its records.

Run it with run.sh (Linux/macOS) or run.ps1 (Windows), or:
    python sivep_ui.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableView,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

import sivep_core

PROJECT_DIR = sivep_core.project_dir()
DOWNLOADS_DIR = PROJECT_DIR / "downloads"


# --------------------------------------------------------------------------- #
# Background worker: runs the (blocking) automation off the GUI thread.
# --------------------------------------------------------------------------- #

class RunWorker(QThread):
    message = Signal(str)
    finished_ok = Signal(list)
    failed = Signal(str)

    def __init__(self, ano, tipos, headless, exportar_paciente, ultima_semana, timeout_s):
        super().__init__()
        self._ano = ano
        self._tipos = tipos
        self._headless = headless
        self._exportar_paciente = exportar_paciente
        self._ultima_semana = ultima_semana
        self._timeout_s = timeout_s
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            files = sivep_core.run_exports_sync(
                self._ano,
                self._tipos,
                headless=self._headless,
                exportar_dados_paciente=self._exportar_paciente,
                somente_ultima_semana=self._ultima_semana,
                slow_mo_ms=0 if self._headless else 200,
                processing_timeout_s=self._timeout_s,
                log=self.message.emit,
                should_cancel=lambda: self._cancel,
            )
            self.finished_ok.emit([str(f) for f in files])
        except Exception as exc:  # surface any failure to the UI
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class FaixaWorker(QThread):
    """Background worker for the 'Distribuição por faixa etária' Excel export."""

    message = Signal(str)
    finished_ok = Signal(list)
    failed = Signal(str)

    def __init__(self, units, headless):
        super().__init__()
        self._units = units
        self._headless = headless
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            files = sivep_core.run_faixa_etaria_sync(
                units=self._units,
                headless=self._headless,
                slow_mo_ms=0 if self._headless else 200,
                log=self.message.emit,
                should_cancel=lambda: self._cancel,
            )
            self.finished_ok.emit([str(f) for f in files])
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------- #
# DBF table model.
# --------------------------------------------------------------------------- #

class DbfTableModel(QAbstractTableModel):
    def __init__(self, fields: list[str], rows: list[list]):
        super().__init__()
        self._fields = fields
        self._rows = rows

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self._fields)

    def data(self, index, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and index.isValid():
            val = self._rows[index.row()][index.column()]
            return "" if val is None else str(val)
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            return self._fields[section]
        return section + 1


# --------------------------------------------------------------------------- #
# Runner tab.
# --------------------------------------------------------------------------- #

class RunnerTab(QWidget):
    def __init__(self, on_run_finished):
        super().__init__()
        self._on_run_finished = on_run_finished
        self._worker: RunWorker | None = None

        layout = QVBoxLayout(self)

        # Credentials (loaded from .env if present, saved on run for next time).
        cred = QGroupBox("Credenciais")
        cred_l = QHBoxLayout(cred)
        _login, _senha = sivep_core.load_credentials()
        cred_l.addWidget(QLabel("Login:"))
        self.login = QLineEdit(_login)
        cred_l.addWidget(self.login, 1)
        cred_l.addWidget(QLabel("Senha:"))
        self.senha = QLineEdit(_senha)
        self.senha.setEchoMode(QLineEdit.Password)
        cred_l.addWidget(self.senha, 1)
        layout.addWidget(cred)

        cfg = QGroupBox("Configuração")
        cfg_l = QVBoxLayout(cfg)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Ano epidemiológico:"))
        self.ano = QLineEdit("2024")
        self.ano.setMaximumWidth(100)
        row1.addWidget(self.ano)
        row1.addStretch()
        cfg_l.addLayout(row1)

        fichas = QHBoxLayout()
        fichas.addWidget(QLabel("Tipos de ficha:"))
        self.chk_srag_hosp = QCheckBox("SRAG Hospitalizado")
        self.chk_srag_hosp.setChecked(True)
        self.chk_sg = QCheckBox("SG")
        self.chk_sg.setChecked(True)
        # SRAG UTI is intentionally not offered (only SRAG Hospitalizado and SG allowed).
        for c in (self.chk_srag_hosp, self.chk_sg):
            fichas.addWidget(c)
        fichas.addStretch()
        cfg_l.addLayout(fichas)

        opts = QHBoxLayout()
        self.chk_paciente = QCheckBox("Exportar dados do paciente")
        self.chk_paciente.setChecked(True)
        self.chk_ultima_semana = QCheckBox("Somente a última semana")
        self.chk_ultima_semana.setChecked(False)
        self.chk_ultima_semana.setToolTip(
            "Exporta apenas a semana epidemiológica mais recente, em vez do ano inteiro."
        )
        self.chk_headless = QCheckBox("Navegador invisível (headless)")
        self.chk_headless.setChecked(False)
        opts.addWidget(self.chk_paciente)
        opts.addWidget(self.chk_ultima_semana)
        opts.addWidget(self.chk_headless)
        opts.addStretch()
        cfg_l.addLayout(opts)

        tout = QHBoxLayout()
        tout.addWidget(QLabel("Timeout de processamento (s):"))
        self.timeout = QSpinBox()
        self.timeout.setRange(60, 7200)
        self.timeout.setValue(3600)
        self.timeout.setMaximumWidth(120)
        tout.addWidget(self.timeout)
        tout.addStretch()
        cfg_l.addLayout(tout)

        layout.addWidget(cfg)

        btns = QHBoxLayout()
        self.btn_run = QPushButton("▶ Executar")
        self.btn_run.clicked.connect(self._start)
        self.btn_stop = QPushButton("■ Parar")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop)
        btns.addWidget(self.btn_run)
        btns.addWidget(self.btn_stop)
        btns.addStretch()
        layout.addLayout(btns)

        layout.addWidget(QLabel("Log:"))
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log, 1)

    def _tipos(self) -> dict[str, str]:
        mapping = {
            "3": ("SRAG_Hospitalizado", self.chk_srag_hosp),
            "1": ("SG", self.chk_sg),
        }
        return {v: name for v, (name, chk) in mapping.items() if chk.isChecked()}

    def _start(self):
        login = self.login.text().strip()
        senha = self.senha.text()
        if not (login and senha):
            QMessageBox.warning(self, "Credenciais", "Informe login e senha.")
            return
        # Persist credentials to .env so they are reused next time.
        sivep_core.save_credentials(login, senha)

        ano = self.ano.text().strip()
        if not (ano.isdigit() and len(ano) == 4):
            QMessageBox.warning(self, "Ano inválido", "Informe um ano com 4 dígitos, ex: 2024.")
            return
        tipos = self._tipos()
        if not tipos:
            QMessageBox.warning(self, "Sem fichas", "Selecione ao menos um tipo de ficha.")
            return

        self.log.clear()
        self._set_running(True)
        self._worker = RunWorker(
            ano,
            tipos,
            headless=self.chk_headless.isChecked(),
            exportar_paciente=self.chk_paciente.isChecked(),
            ultima_semana=self.chk_ultima_semana.isChecked(),
            timeout_s=self.timeout.value(),
        )
        self._worker.message.connect(self._append)
        self._worker.finished_ok.connect(self._done)
        self._worker.failed.connect(self._error)
        self._worker.start()

    def _stop(self):
        if self._worker:
            self._append(">> Parando após a etapa atual…")
            self._worker.cancel()
            self.btn_stop.setEnabled(False)

    def _append(self, text: str):
        self.log.appendPlainText(text)

    def _done(self, files: list[str]):
        self._set_running(False)
        if files:
            self._append(f">> Concluído. {len(files)} arquivo(s) baixado(s).")
        else:
            self._append(">> Concluído sem downloads.")
        self._on_run_finished()

    def _error(self, msg: str):
        self._set_running(False)
        self._append(f">> ERRO: {msg}")
        QMessageBox.critical(self, "Erro na execução", msg)

    def _set_running(self, running: bool):
        self.btn_run.setEnabled(not running)
        self.btn_stop.setEnabled(running)


# --------------------------------------------------------------------------- #
# DBF viewer tab.
# --------------------------------------------------------------------------- #

class ViewerTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        self.btn_refresh = QPushButton("⟳ Atualizar lista")
        self.btn_refresh.clicked.connect(self.refresh_list)
        self.btn_open = QPushButton("📂 Abrir .dbf…")
        self.btn_open.clicked.connect(self._open_dialog)
        top.addWidget(self.btn_refresh)
        top.addWidget(self.btn_open)
        top.addStretch()
        self.encoding = QComboBox()
        self.encoding.addItems(["latin-1", "cp850", "utf-8"])
        top.addWidget(QLabel("Encoding:"))
        top.addWidget(self.encoding)
        layout.addLayout(top)

        splitter = QSplitter(Qt.Horizontal)

        left = QWidget()
        left_l = QVBoxLayout(left)
        left_l.setContentsMargins(0, 0, 0, 0)
        left_l.addWidget(QLabel("downloads/"))
        self.file_list = QListWidget()
        self.file_list.itemDoubleClicked.connect(
            lambda it: self._load(DOWNLOADS_DIR / it.text())
        )
        left_l.addWidget(self.file_list)
        splitter.addWidget(left)

        right = QWidget()
        right_l = QVBoxLayout(right)
        right_l.setContentsMargins(0, 0, 0, 0)
        self.info = QLabel("Nenhum arquivo carregado.")
        right_l.addWidget(self.info)
        self.table = QTableView()
        self.table.setAlternatingRowColors(True)
        right_l.addWidget(self.table, 1)
        splitter.addWidget(right)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([240, 760])
        layout.addWidget(splitter, 1)

        self.refresh_list()

    def refresh_list(self):
        self.file_list.clear()
        if DOWNLOADS_DIR.exists():
            files = sorted(
                (p for p in DOWNLOADS_DIR.iterdir() if p.suffix.lower() == ".dbf"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for p in files:
                self.file_list.addItem(p.name)
        if self.file_list.count() == 0:
            self.info.setText("Nenhum .dbf em downloads/ ainda.")

    def _open_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Abrir DBF", str(DOWNLOADS_DIR), "DBF files (*.dbf *.DBF);;All files (*)"
        )
        if path:
            self._load(Path(path))

    def _load(self, path: Path):
        try:
            import dbfread

            table = dbfread.DBF(
                str(path),
                encoding=self.encoding.currentText(),
                load=False,
                char_decode_errors="replace",
            )
            fields = list(table.field_names)
            rows = [[rec.get(f) for f in fields] for rec in table]
        except Exception as exc:
            QMessageBox.critical(self, "Erro ao ler DBF", f"{type(exc).__name__}: {exc}")
            return

        model = DbfTableModel(fields, rows)
        self.table.setModel(model)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.resizeColumnsToContents()
        self.info.setText(f"{path.name}  —  {len(rows)} registros × {len(fields)} colunas")


# --------------------------------------------------------------------------- #
# Faixa Etária tab — Distribuição dos vírus respiratórios por faixa etária.
# --------------------------------------------------------------------------- #

class FaixaEtariaTab(QWidget):
    def __init__(self):
        super().__init__()
        self._worker: FaixaWorker | None = None
        layout = QVBoxLayout(self)

        faixas_str = ", ".join(
            f"{ini}-{fim if fim else '+'}" for ini, fim in sivep_core.FAIXAS_ETARIAS
        )
        info = QLabel(
            "Exporta para Excel a 'Distribuição dos vírus respiratórios por faixa etária'\n"
            f"(todos os vírus, IFI+PCR, faixas {faixas_str}, última semana),\n"
            "uma exportação por ficha (SG e SRAG UTI) para cada unidade selecionada."
        )
        layout.addWidget(info)

        box = QGroupBox("Unidades (US)")
        box_l = QHBoxLayout(box)
        # One checkbox per unit defined in core (US 165 is absent there by design).
        self._unit_checks = {}
        for us in sivep_core.UNIDADES_US:
            cb = QCheckBox(f"US {us}")
            cb.setChecked(True)
            self._unit_checks[us] = cb
            box_l.addWidget(cb)
        box_l.addStretch()
        layout.addWidget(box)

        opts = QHBoxLayout()
        self.chk_headless = QCheckBox("Navegador invisível (headless)")
        opts.addWidget(self.chk_headless)
        opts.addStretch()
        layout.addLayout(opts)

        btns = QHBoxLayout()
        self.btn_run = QPushButton("▶ Exportar Excel")
        self.btn_run.clicked.connect(self._start)
        self.btn_stop = QPushButton("■ Parar")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop)
        btns.addWidget(self.btn_run)
        btns.addWidget(self.btn_stop)
        btns.addStretch()
        layout.addLayout(btns)

        layout.addWidget(QLabel("Log:"))
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log, 1)

    def _start(self):
        login, senha = sivep_core.load_credentials()
        if not (login and senha):
            QMessageBox.warning(
                self, "Credenciais", "Informe login e senha na aba Runner primeiro."
            )
            return
        units = [us for us, cb in self._unit_checks.items() if cb.isChecked()]
        if not units:
            QMessageBox.warning(self, "Unidades", "Selecione ao menos uma unidade.")
            return
        self.log.clear()
        self._set_running(True)
        self._worker = FaixaWorker(units, headless=self.chk_headless.isChecked())
        self._worker.message.connect(self.log.appendPlainText)
        self._worker.finished_ok.connect(self._done)
        self._worker.failed.connect(self._error)
        self._worker.start()

    def _stop(self):
        if self._worker:
            self.log.appendPlainText(">> Parando após a etapa atual…")
            self._worker.cancel()
            self.btn_stop.setEnabled(False)

    def _done(self, files):
        self._set_running(False)
        self.log.appendPlainText(f">> Concluído. {len(files)} arquivo(s) Excel salvo(s).")

    def _error(self, msg):
        self._set_running(False)
        self.log.appendPlainText(f">> ERRO: {msg}")
        QMessageBox.critical(self, "Erro na execução", msg)

    def _set_running(self, running):
        self.btn_run.setEnabled(not running)
        self.btn_stop.setEnabled(running)


# --------------------------------------------------------------------------- #
# Main window.
# --------------------------------------------------------------------------- #

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SIVEP-Gripe — Downloader & DBF Viewer")
        self.resize(1000, 680)

        tabs = QTabWidget()
        self.viewer = ViewerTab()
        self.runner = RunnerTab(on_run_finished=self.viewer.refresh_list)
        self.faixa = FaixaEtariaTab()
        tabs.addTab(self.runner, "Runner")
        tabs.addTab(self.faixa, "Faixa Etária")
        tabs.addTab(self.viewer, "DBF Viewer")
        self.setCentralWidget(tabs)


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
