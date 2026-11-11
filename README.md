# Date with a Book

A small web app for preparing "Date with a Book" gifts: look up a book, get a category,
reader age and a short mystery description that hints at the story without giving it away,
keep an inventory (with quantities), export CSV, and print labels.

## Quick start (Ubuntu)

```bash
cp .env.example .env        # then edit .env and paste your Anthropic API key
chmod +x run.sh
./run.sh
```

Open **http://localhost:8000**. From another computer on your home network use
`http://<vm-ip-address>:8000` (find the IP with `hostname -I`).

Requirements: Python 3.10+ (`sudo apt install python3-venv` if `run.sh` complains about venv).

## Using it

**Add a Book**
- Type an **ISBN** (hyphens fine) or a **title** (author optional) and press *Look up*.
  For titles, pick the right book from the list.
- **Upload photo…** — a picture of the cover, the back (barcode) or the copyright page.
- **Scan barcode (webcam)** — hold the back of the book to the camera.
- The app fills in title, author, first-published year and pages, then writes the
  category, subgenre, reader age and mystery description. Everything is editable.
- *Regenerate description* writes a fresh one; type a nudge first (“spookier”, “more romantic”)
  to steer it.
- If the book is already in inventory you'll see its code and an **Add 1 more copy** button
  instead of creating a duplicate.

**Inventory** — sort by date entered (newest/oldest), title, category or quantity; search;
use − / + to change quantities (every change is logged with a date — see *Edit*).
Tick rows, then **Export CSV** or **Print labels** (nothing ticked = all rows shown).

**Labels** — choose a label stock preset or set a custom size under *Size & layout*.
Tick which fields print (category, subgenre, reader age, description, inventory code),
optionally a header line and border. *Skip first* lets you reuse a partly-used sheet;
*One per copy* prints a label for every copy in stock. *Save as default* remembers the setup.
Do a test print on plain paper first; in the print dialog choose **Margins: None** and **Scale 100%**.

**Settings** — edit the category list the AI chooses from.

## Notes

- **Cost:** each new description is one Claude API call (Claude Opus 5.5) — roughly 1–3 cents.
  Photo reading is a second, smaller call. Book lookups (Open Library) are free.
- **Webcam scanning** only works when the page is opened as `http://localhost:8000` on the
  same machine, or over `https://`. Browsers block cameras on plain `http://<ip>` addresses.
  “Upload photo” works everywhere.
- **Data** lives in `data/dwab.db` (SQLite). Back up that one file. Inventory codes look like
  `DWB-0042` and never change.
- There is **no login** yet — keep it on your home network.
- If the "First published" year from the book database disagrees with the AI's knowledge,
  a warning is shown so you can check it.

## Development

```bash
.venv/bin/python -m pytest -q
```

Layout: `app/main.py` (API), `app/lookup.py` (Open Library / Google Books), `app/ai.py`
(Claude prompts, leak check), `app/db.py` (SQLite), `app/static/` (front end, label page).
