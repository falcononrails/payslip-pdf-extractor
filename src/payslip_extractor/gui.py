from __future__ import annotations

import os
from collections.abc import Iterable
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
        pdf_paths = _select_pdf_paths(root)
        root.withdraw()
        root.update()

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


def _select_pdf_paths(root: object) -> list[Path]:
    import tkinter as tk
    from tkinter import filedialog, messagebox

    selected_paths: list[Path] = []
    result: dict[str, list[Path] | None] = {"paths": None}
    last_dir: Path | None = None

    root.title("Payslip PDF Extractor")
    root.geometry("840x460")
    root.minsize(680, 360)

    container = tk.Frame(root, padx=16, pady=16)
    container.pack(fill=tk.BOTH, expand=True)

    title = tk.Label(
        container,
        text="Selectionner les fichiers PDF",
        font=("TkDefaultFont", 13, "bold"),
        anchor="w",
    )
    title.pack(fill=tk.X)

    instructions = tk.Label(
        container,
        text="Ajoutez des fichiers plusieurs fois pour choisir des PDFs depuis plusieurs dossiers.",
        anchor="w",
    )
    instructions.pack(fill=tk.X, pady=(4, 12))

    list_frame = tk.Frame(container)
    list_frame.pack(fill=tk.BOTH, expand=True)

    scrollbar = tk.Scrollbar(list_frame, orient=tk.VERTICAL)
    listbox = tk.Listbox(list_frame, selectmode=tk.EXTENDED, yscrollcommand=scrollbar.set)
    scrollbar.config(command=listbox.yview)
    listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    count_label = tk.Label(container, text="0 PDF selectionne", anchor="w")
    count_label.pack(fill=tk.X, pady=(8, 0))

    button_row = tk.Frame(container)
    button_row.pack(fill=tk.X, pady=(12, 0))

    def refresh_list() -> None:
        listbox.delete(0, tk.END)
        for path in selected_paths:
            listbox.insert(tk.END, str(path))

        count = len(selected_paths)
        plural = "s" if count != 1 else ""
        count_label.config(text=f"{count} PDF{plural} selectionne{plural}")
        continue_button.config(state=tk.NORMAL if selected_paths else tk.DISABLED)

    def add_pdfs() -> None:
        nonlocal last_dir
        dialog_options = {
            "parent": root,
            "title": "Ajouter des fichiers PDF",
            "filetypes": [("PDF files", "*.pdf"), ("All files", "*.*")],
        }
        if last_dir is not None:
            dialog_options["initialdir"] = str(last_dir)

        paths = filedialog.askopenfilenames(**dialog_options)
        if not paths:
            return

        last_dir = Path(paths[-1]).expanduser().parent
        added = add_unique_pdf_paths(selected_paths, paths)
        refresh_list()
        if added == 0:
            messagebox.showinfo("Aucun nouveau PDF", "Tous les fichiers selectionnes etaient deja dans la liste.")

    def add_folder() -> None:
        nonlocal last_dir
        dialog_options = {"parent": root, "title": "Ajouter un dossier de PDFs"}
        if last_dir is not None:
            dialog_options["initialdir"] = str(last_dir)

        folder = filedialog.askdirectory(**dialog_options)
        if not folder:
            return

        last_dir = Path(folder).expanduser()
        pdfs = pdf_files_in_folder(last_dir)
        added = add_unique_pdf_paths(selected_paths, pdfs)
        refresh_list()
        if added == 0:
            messagebox.showinfo("Aucun PDF ajoute", "Aucun nouveau fichier PDF n'a ete trouve dans ce dossier.")

    def remove_selected() -> None:
        selected_indexes = list(listbox.curselection())
        if not selected_indexes:
            return
        for index in reversed(selected_indexes):
            del selected_paths[index]
        refresh_list()

    def clear_selected() -> None:
        selected_paths.clear()
        refresh_list()

    def continue_selection() -> None:
        if not selected_paths:
            messagebox.showwarning("Aucun PDF", "Ajoutez au moins un fichier PDF.")
            return
        result["paths"] = list(selected_paths)
        root.quit()

    def cancel_selection() -> None:
        result["paths"] = None
        root.quit()

    tk.Button(button_row, text="Ajouter des PDFs...", command=add_pdfs).pack(side=tk.LEFT)
    tk.Button(button_row, text="Ajouter un dossier...", command=add_folder).pack(side=tk.LEFT, padx=(8, 0))
    tk.Button(button_row, text="Retirer selection", command=remove_selected).pack(side=tk.LEFT, padx=(8, 0))
    tk.Button(button_row, text="Vider", command=clear_selected).pack(side=tk.LEFT, padx=(8, 0))

    tk.Button(button_row, text="Annuler", command=cancel_selection).pack(side=tk.RIGHT)
    continue_button = tk.Button(button_row, text="Continuer", command=continue_selection)
    continue_button.pack(side=tk.RIGHT, padx=(0, 8))

    root.protocol("WM_DELETE_WINDOW", cancel_selection)
    refresh_list()
    root.deiconify()
    root.lift()
    root.mainloop()

    if result["paths"] is None:
        raise UserCancelled("No PDF files selected.")

    for child in root.winfo_children():
        child.destroy()

    return result["paths"]


def add_unique_pdf_paths(existing_paths: list[Path], candidate_paths: Iterable[Path | str]) -> int:
    seen = {_path_key(path) for path in existing_paths}
    added = 0

    for candidate in candidate_paths:
        path = Path(candidate).expanduser().resolve(strict=False)
        if path.suffix.lower() != ".pdf":
            continue

        key = _path_key(path)
        if key in seen:
            continue

        existing_paths.append(path)
        seen.add(key)
        added += 1

    return added


def pdf_files_in_folder(folder: Path) -> list[Path]:
    if not folder.exists() or not folder.is_dir():
        return []

    return sorted(
        (path for path in folder.iterdir() if path.is_file() and path.suffix.lower() == ".pdf"),
        key=lambda path: path.name.casefold(),
    )


def _path_key(path: Path) -> str:
    resolved = Path(path).expanduser().resolve(strict=False)
    value = str(resolved)
    return value.casefold() if os.name == "nt" else value
