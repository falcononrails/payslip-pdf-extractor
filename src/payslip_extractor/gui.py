from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class UserCancelled(RuntimeError):
    """Raised when a GUI selection dialog is cancelled."""


@dataclass(frozen=True)
class GuiSelections:
    pdf_paths: list[Path]
    numbers_file: Path
    output_dir: Path
    mode: str


def collect_gui_selections(ask_mode: bool = True) -> GuiSelections:
    import tkinter as tk
    from tkinter import filedialog, messagebox

    root = tk.Tk()
    root.withdraw()
    root.update()

    try:
        pdf_paths = [
            Path(path)
            for path in filedialog.askopenfilenames(
                parent=root,
                title="Selectionner les fichiers PDF",
                filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
            )
        ]
        if not pdf_paths:
            raise UserCancelled("No PDF files selected.")

        numbers_file = filedialog.askopenfilename(
            parent=root,
            title="Selectionner le fichier Excel ou CSV",
            filetypes=[
                ("Excel/CSV files", "*.xlsx *.xlsm *.csv"),
                ("Excel files", "*.xlsx *.xlsm"),
                ("CSV files", "*.csv"),
                ("All files", "*.*"),
            ],
        )
        if not numbers_file:
            raise UserCancelled("No number file selected.")

        output_dir = filedialog.askdirectory(parent=root, title="Selectionner le dossier de sortie")
        if not output_dir:
            raise UserCancelled("No output folder selected.")

        mode = "separate"
        if ask_mode:
            merged = messagebox.askyesnocancel(
                "Mode de sortie",
                "Creer un seul PDF fusionne ?\n\nOui = un seul PDF\nNon = un PDF par numero",
                parent=root,
            )
            if merged is None:
                raise UserCancelled("No output mode selected.")
            mode = "merged" if merged else "separate"

        return GuiSelections(
            pdf_paths=pdf_paths,
            numbers_file=Path(numbers_file),
            output_dir=Path(output_dir),
            mode=mode,
        )
    finally:
        root.destroy()
