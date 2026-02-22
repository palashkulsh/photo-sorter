## Photo Sorter (manual sequence by rename)

the main aim to create this project is to be able to sort and rename pics in a folder in my order. i had around 200 pics which i had to reorder in my order to share it to photographer for printing in album. to make thing straightforward and confusion free i thought of providing ordered pics so that they can be arranged in printed album by photographer himself. hence this project was born

you manually arrange and reorder the pics. it will keep a note of all the rearrangements. once done you have to click on commit/rearrange button upon which the pics will be actually rearranged by renaming them in order along with prefix(provided in top bar)

simple !!

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


