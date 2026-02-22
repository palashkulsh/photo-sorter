## Photo Sorter (manual sequence by rename)

This is a small **PySide6** desktop app to:

- Select a **single folder** containing photos (no subfolders)
- **Drag & drop** thumbnails to arrange them in your preferred order
- Persist that order in a hidden file inside the folder (no renames yet)
- On **Commit/Rearrange**, rename files to match the order using a prefix and 5-digit zero padding (starting at 0)

### Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Run

```bash
python -m photo_sorter
```

### Order file

The app writes the order into the selected folder:

- `.photo-sorter-order.json`


