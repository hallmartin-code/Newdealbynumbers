# TEN Capital Investor One-Pager Generator

A local Streamlit app that reads an investor pitch deck, has Claude review it as a
startup financial specialist, lets you correct the figures and approve any
estimates, and exports a one-page investor PDF plus the structured analysis as
JSON.

Every figure in the analysis is labelled **reported** (with its slide or page),
**calculated** (with its formula), **estimated** (with method, assumptions,
range and confidence), **not provided**, or **conflicting** (with both values).
Estimates never reach the PDF until you approve them.

## Setup

Requires Python 3.11 or later.

```bash
cd ten_onepager
python -m venv .venv
# Windows (PowerShell):  .venv\Scripts\Activate.ps1
# macOS / Linux:         source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env      # macOS / Linux: cp .env.example .env
```

Put your Anthropic API key in `.env` as `ANTHROPIC_API_KEY=...`. Developing in
Claude Code does not give the app runtime credentials, so it needs its own key.
Without a key, the app runs in **demo mode** (see below).

## Launch

```bash
streamlit run app.py
```

The app opens at http://localhost:8501.

## Using the app

1. **Upload deck.** Choose a PDF, PPTX or PPT file and click **Extract text**,
   or click **Use fictional sample deck**. The extracted text is shown by page
   or slide, with warnings for scanned pages.
2. **Analyze.** The app states that the deck text will be sent to Anthropic
   and names the model. Tick the consent box, then click **Analyze deck**.
3. **Review.** For each of the nine sections, edit the summary, metrics,
   points, team, allocations and gaps. Tick **approve estimate** to allow an
   estimate into the PDF after checking its assumptions, which are listed under
   each table. To resolve a conflict, enter the correct value and set evidence
   to `reported` with its slide. The validation panel lists errors, warnings
   and notes.
4. **Export.** Click **Generate one-page PDF**, check the preview, and download
   the PDF and the analysis JSON. If you edit anything afterwards, generate the
   PDF again.

**Clear session** in the sidebar forgets the deck, analysis and edits.

### Demo mode

**Load demo analysis (fictional sample data)** loads a hand-written analysis of
`samples/sample_deck.pptx`, a deck for an invented company (Northpeak Cold
Chain). Demo mode is labelled in the app and on the PDF. It needs no API key.

### Settings (sidebar)

| Setting | Default | Notes |
|---|---|---|
| Model | `claude-opus-5-5` | Or set `CLAUDE_MODEL` in `.env`. |
| OCR scanned PDF pages | off | Needs Tesseract; the sidebar says whether it is available. |
| External research | **off** | Lets the model use web search. Each source keeps its URL and access date. Citations whose URL did not come back from an actual search are removed, and the figures that cited them become unapproved estimates. |
| Exit valuation scenarios | off | Exit values and investor returns are produced only when this is on, always as estimates with explicit assumptions. |

## Supported formats

| Format | How it is read |
|---|---|
| PDF | PyMuPDF text with page references. Pages that are images with little text are flagged as scanned; with OCR on and Tesseract installed, they are read with OCR. Password-protected PDFs are rejected with a message. |
| PPTX | python-pptx: text boxes, grouped shapes, tables, chart data (categories and series values) and speaker notes, with slide references. |
| PPT (legacy) | Converted to PPTX with LibreOffice when it is installed. Otherwise the app explains how to save the file as PPTX or PDF. |

## What the analysis enforces

- **All nine sections:** problem, solution, team, traction, market size, competitive advantage, fundraise, use of funds and exit.
- **No numbers forced into a section.** When no defensible inputs exist, the section gets a qualitative statement and a list of what is missing.
- **Never estimated:** founder credentials, actual revenue, customer counts, regulatory approvals, funding commitments and investment terms. An estimate of these is flagged as an error and kept out of the PDF even if approved.
- **Look-alike metrics are kept apart:**
  - pilots and paying customers
  - revenue, ARR, bookings, GMV and pipeline
  - signed commitments, verbal interest and cash received
  - actual and forecast results
  - TAM, SAM and SOM
- **Validation** checks currencies (never converted), units, display values against numeric values, periods, percentage bounds, use-of-funds totals (percentages and amounts), raise minus cash received against the amount remaining, pre-money plus raise against post-money, TAM ≥ SAM ≥ SOM, and the arithmetic of every calculated figure.
- **Conflicts are never reconciled silently,** and unknown values are never treated as zero.
- **Model output is validated against the Pydantic schema** in `onepager/models.py`. Malformed output gets one repair attempt, then a clear error.
- **Long decks are processed in chunks.** Decks over `ONEPAGER_MAX_CHARS` (default 150,000 characters) are condensed into facts, each keeping its original page or slide reference. A reference the model invents is marked "unverified".

## The PDF

- **Layout:** exactly one US Letter page. It has the company name, a one-sentence thesis, a strip of 3–5 key metrics and all nine sections in two columns.
- **Footer:** the TEN footer line ("Compiled on … by TEN Capital Network", with the logo) and "Confidential – for recipients only."
- **Text:** Open Sans (bundled under the SIL Open Font License, with a Helvetica fallback). Body text is 9 pt, section headings are 10.5 pt uppercase, and the colors follow the TEN brand palette.
- **Labels:** reported figures carry small slide/page references (`[S4]`, `[p.4]`). Approved estimates, forecasts and proposed allocations carry visible `EST.`, `FORECAST` and `PROPOSED` chips. Calculated figures are marked `calc.`.
- **Fitting:** when content is too long, the tallest sections are shortened one step at a time: repeated figures are removed, then extra points, then extra metrics, then summaries are cut to their first sentence. Body text is never shrunk below 9 pt, and nothing is clipped. If it still does not fit, the app names the sections to shorten.

## Privacy

- **Nothing is sent before consent.** Deck text goes to the configured AI provider (Anthropic) only after you tick the consent box and click Analyze. The app says so before sending.
- **Uploads are deleted after extraction.** Each upload is written to a private temporary folder, which is removed as soon as extraction finishes.
- **Nothing sensitive is logged.** Deck contents and credentials are not logged.
- **No usage statistics.** Streamlit's usage statistics are turned off in `.streamlit/config.toml`.

## Files

```
app.py                     Streamlit interface (upload, analyze, review, export)
onepager/config.py         Settings from environment / .env
onepager/extraction.py     PDF, PPTX and PPT extraction with page/slide references, OCR
onepager/models.py         Pydantic schema and evidence labels
onepager/analysis.py       Anthropic API call, chunking, schema validation and repair
onepager/evidence.py       Rules for what may appear in the PDF
onepager/validation.py     Financial validation and amount/percent parsing
onepager/pdf.py            One-page ReportLab layout and fitting
onepager/export.py         JSON export
onepager/demo.py           Demo-mode loader
assets/                    TEN logo, Open Sans fonts and licence
samples/                   Fictional sample deck (PPTX, PDF, PPT), demo analysis,
                           sample one-pager PDF and JSON, and a live example
                           (samples/live_example) produced by claude-opus-5-5
tests/                     pytest suite
```

## Tests

```bash
python -m pytest -q
```

The tests cover:

- **Extraction:** PPTX text, tables, charts and notes; PDF pages; scanned-page detection; OCR and LibreOffice fallbacks; PPT conversion when LibreOffice is present.
- **Evidence:** labels and PDF eligibility.
- **Financial checks:** parsing and validation.
- **Analysis pipeline:** uses a fake API client to cover repair, API errors and chunking.
- **PDF:** exactly one page, no unapproved estimates, body text at least 9 pt, shortening and overflow reporting, JSON export.
- **Streamlit:** a headless run of the demo flow.

To regenerate the sample deck: `python samples/make_sample_deck.py --pdf --ppt`
(the `--pdf`/`--ppt` conversions need LibreOffice).

## Limitations

- OCR needs Tesseract, which is not bundled. Text inside images in PPTX files is not read; export the deck to PDF and enable OCR instead.
- Chart values are read from native PowerPoint charts. Charts in PDFs, and charts pasted as images, give only the text and axis labels PyMuPDF can find.
- Currencies are flagged when mixed but never converted.
- Validation checks arithmetic and consistency in what it is given. It cannot tell whether the deck's own figures are true.
- Analysis quality depends on the model. Review every figure before sharing the PDF.
