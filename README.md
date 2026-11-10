# Papaver

**English** | [Русский](README.ru.md) | [Українська](README.uk.md)

**Papaver** is an asynchronous command-line auditor for the integrity of scientific
publications. Give it a paper's DOI and it collects, from open bibliographic
databases, what you need for a quick due-diligence check: whether the paper has been
retracted, whether it carries corrections or expressions of concern, where and by
whom it was published, and how other researchers cite it.

A typical use: before you cite a paper or build on its findings, run
`papaver check <DOI>`. Within a few seconds you can see, for example, that
Wakefield's 1998 paper on the alleged link between the MMR vaccine and autism was
retracted by The Lancet in 2010, and that recent papers citing it use it as an
example of research fraud.

## Features

Current version (stage 2):

- **Metadata from two sources in parallel.** Crossref (the DOI registration agency)
  and OpenAlex are queried concurrently and merged into one strictly typed
  `PaperMetadata` model: title, year, journal, ISSN, publisher, authors with ORCID /
  OpenAlex IDs and affiliations, open-access status, citation count.
- **Retractions and editorial notices.** Retractions, corrections, expressions of
  concern, withdrawals and other notices come from Crossref's `updated-by` field
  (including Retraction Watch records, `source: retraction-watch`) and from the
  OpenAlex `is_retracted` flag. Each notice keeps its type, date, notice DOI and
  source.
- **Citation contexts from Semantic Scholar.** The sentences in which other papers
  cite the one being checked, together with the citation intent (`background`,
  `methodology`, `result`) and whether the citation is influential
  (`isInfluential`). Empty and junk contexts are filtered out.
- **Local cache** of API responses in SQLite (SQLAlchemy 2.0 async + aiosqlite) with
  a TTL: checking the same paper again sends no network requests.
- **Fault tolerance.** Timeouts, retries with exponential backoff, `Retry-After`
  handling on HTTP 429, and rate limiting for Semantic Scholar. If one source is
  down, the report is built from the others and carries a warning that the data is
  incomplete.
- **Two output formats:** a readable summary in the terminal, and the full report as
  JSON (`--json`) for further processing.

Planned (see the [roadmap](#roadmap)): PubPeer discussions, a traffic-light verdict
(`BLACK_CODE` / `RED` / `YELLOW` / `GREEN`), journal and author reputation, a
`papaver verify "<claim>"` command, lookup by PMID and URL.

## Requirements

- Python **3.11+**
- Internet access (api.crossref.org, api.openalex.org, api.semanticscholar.org)

## Installation

### 1. Get the code

```bash
git clone https://github.com/Prostochell-544/Papaver.git
cd Papaver
```

Without git: download the repository archive (**Code → Download ZIP** on GitHub) and
unpack it.

### 2. Create and activate a virtual environment

Windows (PowerShell):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell refuses to run scripts, use `cmd` with `.venv\Scripts\activate.bat`,
or call the interpreter directly: `.venv\Scripts\python.exe`.

Linux / macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

**PyCharm:** *Settings → Project → Python Interpreter → Add Interpreter → Existing*,
then select `.venv\Scripts\python.exe` (or `.venv/bin/python`).

### 3. Install the dependencies and the package

```bash
pip install -r requirements.txt
pip install -e .
```

`pip install -e .` installs the package in development mode: edits under `src/` take
effect without reinstalling, and the `papaver` command becomes available in the
environment.

### 4. Configure `.env`

Copy the template:

```powershell
Copy-Item .env.example .env        # Windows PowerShell
```

```bash
cp .env.example .env               # Linux / macOS
```

and fill in the values. `.env` is read from the **current working directory**, and
real environment variables take precedence over it. `.env` is excluded from git.

| Variable | Default | Purpose |
|---|---|---|
| `PAPAVER_CONTACT_EMAIL` | — | Your email. It goes into the header `User-Agent: Papaver/0.1.0 (mailto:...)`. Crossref and OpenAlex route such requests to their **polite pool**, which is faster and more reliable than the anonymous one. Strongly recommended. |
| `PAPAVER_S2_API_KEY` | — | A [Semantic Scholar API](https://www.semanticscholar.org/product/api) key (free, on request). Without a key, requests go to a shared pool that often answers HTTP 429. Papaver retries, but a key makes it much more reliable. |
| `PAPAVER_DB_URL` | `sqlite+aiosqlite:///./papaver_cache.db` | Database for the response cache. A relative path is resolved against the working directory. |
| `PAPAVER_CACHE_TTL_HOURS` | `24` | How many hours a cached response stays fresh. `0` means never read from the cache. |
| `PAPAVER_S2_MAX_CITATIONS` | `1000` | How many citing papers to scan (at most 9999, an API limit). |
| `PAPAVER_HTTP_TIMEOUT` | `15` | Timeout for a single request, in seconds. |
| `PAPAVER_MAX_RETRIES` | `2` | Retries for Crossref / OpenAlex. |
| `PAPAVER_S2_MAX_RETRIES` | `4` | Retries for Semantic Scholar. |
| `PAPAVER_S2_MIN_INTERVAL` | `1.0` | Minimum interval between Semantic Scholar requests, in seconds. |

## Usage

```bash
papaver check "10.1016/S0140-6736(97)11096-0"
```

> **Quote the DOI.** Many DOIs contain parentheses, which both PowerShell and bash
> try to interpret.

All common ways of writing a DOI are accepted. Each is reduced to the canonical
lower-case form `10.xxxx/...`:

```text
10.1016/S0140-6736(97)11096-0
doi:10.1016/S0140-6736(97)11096-0
https://doi.org/10.1016/S0140-6736(97)11096-0
https://doi.org/10.1016%2FS0140-6736%2897%2911096-0
https://onlinelibrary.wiley.com/doi/full/10.1002/anie.201915678
```

### `papaver check` options

| Option | Description |
|---|---|
| `--max-citations N` | How many citing papers to scan in Semantic Scholar (0–9999, defaults to `PAPAVER_S2_MAX_CITATIONS`). `0` skips citations entirely. |
| `--contexts N` | How many citation contexts to show in the summary (default 10). Influential citations come first, then the most recent. |
| `--json` | Print the full `AuditReport` as JSON instead of the summary. |
| `--no-cache` | Neither read nor update the local cache. |

Global options go **before** the command:

```bash
papaver --version
papaver -v check "10.1016/S0140-6736(97)11096-0"   # log HTTP requests, retries and cache hits
```

### Example: the Wakefield paper (1998)

```text
$ papaver check "10.1016/S0140-6736(97)11096-0" --contexts 3
PaperMetadata(
    doi='10.1016/s0140-6736(97)11096-0',
    title='RETRACTED: Ileal-lymphoid-nodular hyperplasia, non-specific colitis,
and pervasive developmental disorder in children',
    year=1998,
    journal_name='The Lancet',
    journal_issn=['0140-6736', '0099-5355', '1474-547X'],
    publisher='Elsevier BV',
    openalex_id='W2117847125',
    is_oa=False,
    is_retracted=True,
    update_notices=[
        UpdateNotice(
            update_type=<UpdateType.CORRECTION: 'correction'>,
            label='Correction',
            notice_doi='10.1016/s0140-6736(04)15715-2',
            target_doi='10.1016/s0140-6736(97)11096-0',
            date=datetime.date(2004, 3, 6),
            source='retraction-watch',
            record_id='17269'
        ),
        UpdateNotice(
            update_type=<UpdateType.RETRACTION: 'retraction'>,
            label='Retraction',
            notice_doi='10.1016/s0140-6736(10)60175-4',
            target_doi='10.1016/s0140-6736(97)11096-0',
            date=datetime.date(2010, 2, 6),
            source='retraction-watch',
            record_id='4036'
        )
    ],
    amends=[],
    authors=[
        AuthorInfo(
            name='Andrew J. Wakefield',
            orcid=None,
            openalex_id='A5110236514',
            affiliations=['The Royal Free Hospital']
        ),
        ...                       # 12 more authors
    ],
    cited_by_count=3106,
    sources=[
        <DataSource.CROSSREF: 'crossref'>,
        <DataSource.OPENALEX: 'openalex'>
    ]
)

──────────────────── Citation contexts (Semantic Scholar) ─────────────────────
Scanned 1000 citing papers, more available: 246 with usable contexts, 19
influential.
Intents: background 172, result 7, methodology 6

2024  Disinformation in Science: Ethical Considerations for Citing Retracted
Works
      doi:10.1002/pra2.1025 | influential
      "We do not believe any of our eight articles and subsequent citations
produced as much harm as Wakefield et al. (1998)."
      (+4 more)

2023  Mitigating Vaccine Hesitancy and Building Trust to Prevent Future Measles
Outbreaks in England
      doi:10.3390/vaccines11020288 | background | influential
      "In 1998, Andrew Wakefield and colleagues published a now-infamous
report, which suggested that MMR may predispose infants to gastrointestinal
disease and developmental regression or autism [34]."
      (+3 more)
...
```

How to read it: `is_retracted=True`, and `update_notices` contains a `RETRACTION`
notice dated 6 February 2010 along with the DOI of the retraction notice itself. The
Semantic Scholar section shows how the paper is cited today.

### JSON report

```bash
papaver check "10.1016/S0140-6736(97)11096-0" --json > report.json
```

Top-level fields: `target`, `paper` (`PaperMetadata`), `citations` (`items`,
`scanned`, `truncated`), `warnings`, `verdict` and `reasons` (`null` and empty for
now; the verdict engine will fill them in), `generated_at`.

### Exit codes

| Code | Meaning |
|---|---|
| `0` | The report was built (possibly with warnings about incomplete data). |
| `1` | Neither Crossref nor OpenAlex knows the DOI, or the sources are unreachable. |
| `2` | Invalid DOI, or invalid `PAPAVER_*` settings. |

### Running from PyCharm

`main.py` in the project root wraps the CLI. Create a Run Configuration for
`main.py`, put for example `check "10.1016/S0140-6736(97)11096-0"` in
**Parameters**, and leave **Working directory** at the project root (that is where
`.env` and the cache are read from).

## How it works

```text
DOI ─► normalize_doi()
         │
         ├─► Crossref          /works/{doi}                  ─┐
         ├─► OpenAlex          /works/https://doi.org/{doi}  ─┴─► PaperMetadata ─┐
         └─► Semantic Scholar  /paper/DOI:{doi}/citations    ───► CitationScan  ─┴─► AuditReport
```

All three sources are queried concurrently (`asyncio`), and every request goes
through the SQLite response cache. Semantic Scholar is read in pages of 1000 records,
at most one request per second. The finished `AuditReport` is printed to the
terminal or as JSON. Metadata is mandatory: if neither Crossref nor OpenAlex knows
the DOI, the Semantic Scholar request is cancelled and the command exits with an
error. Citations are optional: if they are unavailable, the report gets a warning
instead.

### Retractions: `updated-by` vs `update-to`

Crossref records the link between a notice and the paper it concerns on both sides:

- the **corrected or retracted paper** has an `updated-by` field listing the notices
  about it. This is where the retraction of the Wakefield paper is recorded;
- the **notice itself** has an `update-to` field pointing at the paper.

Papaver reads both. `PaperMetadata.update_notices` holds notices about the paper
being checked; `PaperMetadata.amends` is filled when the DOI being checked is itself
a notice. Every `UpdateNotice` carries both `notice_doi` and `target_doi`, so the
direction of the link is never ambiguous.

OpenAlex also sets `is_retracted: true` on the retraction notice itself. To keep a
notice from looking like a retracted paper, Papaver relies on Crossref alone for such
records (OpenAlex type `retraction`/`erratum`, or an `update-to` field present).

### Citation contexts

- Semantic Scholar returns at most 1000 records per request and requires
  `offset + limit < 10000`, so a single audit can scan up to 9999 citing papers.
- Many citing papers come back **without contexts** because of publisher licensing
  (about two thirds for the Wakefield paper). They count towards `scanned` but are
  left out of `items`.
- Junk contexts are dropped: anything shorter than 20 characters or with fewer than
  10 letters (`[12]`, `(1–5)`, `...`), and duplicates within one paper (ignoring case
  and whitespace).

### Cache

The cache key is the source plus the full request URL (query parameters included);
the value is the raw JSON response. Only successful responses are cached, never
errors or 404s. A database failure (a locked file, say) does not stop the check: the
cache is switched off with a warning. To clear the cache, delete `papaver_cache.db`
or run the check with `--no-cache`.

## Project structure

```text
Project_TrueFalse/
├── main.py                  # entry point for PyCharm → papaver.cli:app
├── pyproject.toml           # package metadata, `papaver` entry point, pytest settings
├── README.md                # this file; README.ru.md and README.uk.md are translations
├── requirements.txt
├── .env.example             # settings template
├── src/papaver/
│   ├── cli.py               # Typer: `check` command, exit codes, UTF-8 output
│   ├── core/
│   │   ├── config.py        # Settings (pydantic-settings), polite-pool User-Agent
│   │   ├── schemas.py       # Pydantic v2: PaperMetadata, UpdateNotice, CitationScan, AuditReport…
│   │   └── exceptions.py    # PaperNotFoundError, UpstreamTimeoutError, UpstreamServiceError
│   ├── services/
│   │   ├── http.py          # base client: retries, Retry-After, rate limiting, cache
│   │   ├── ingest.py        # Crossref + OpenAlex → PaperMetadata
│   │   ├── citations.py     # Semantic Scholar → CitationContextItem / CitationScan
│   │   └── audit.py         # the `check` pipeline: everything together → AuditReport
│   ├── db/
│   │   ├── models.py        # SQLAlchemy 2.0: the api_cache table
│   │   ├── session.py       # AsyncEngine, async_sessionmaker
│   │   └── cache.py         # ResponseCache with TTL
│   └── utils/
│       ├── doi.py           # DOI normalization (dependency-free, shared with Alcedo)
│       ├── payload.py       # safe access to fields of raw JSON payloads
│       └── formatters.py    # terminal rendering of the report (rich)
└── tests/                   # pytest + respx, no network access
```

The models in `core/schemas.py` are strict (`extra="forbid"`, DOI, ORCID, ISSN and
OpenAlex ID normalized and validated), while messy API responses are cleaned up in
the services. One malformed field in an API response therefore does not break the
whole report.

## Tests

```bash
python -m pytest
```

- The tests **never touch the network**: HTTP requests are intercepted with
  [respx](https://lundberg.github.io/respx/), and the fixtures in
  `tests/conftest.py` are trimmed copies of real Crossref, OpenAlex and Semantic
  Scholar responses.
- Async tests run on `pytest-asyncio` with `asyncio_mode = "auto"` (set in
  `pyproject.toml`), so no decorators are needed.
- The tests do not read your `.env` and use a temporary cache database.

Useful variants:

```bash
python -m pytest -v                          # verbose list of tests
python -m pytest tests/test_citations.py     # a single module
python -m pytest -k "429 or timeout"         # by substring of the test name
```

| Module | What it covers |
|---|---|
| `test_doi.py` | DOI normalization: prefixes, URLs, percent-encoding, parentheses, junk. |
| `test_ingest.py` | Merging Crossref + OpenAlex, retractions, fallback when a source fails, 404, timeouts, retries. |
| `test_citations.py` | Parsing and filtering contexts, pagination, the `offset + limit` limit, API key, 429 / `Retry-After`. |
| `test_cache.py` | Cache reads/writes, TTL, fault tolerance, no repeated HTTP requests. |
| `test_audit.py` | The whole `check` pipeline and the CLI: output, `--json`, exit codes. |

In PyCharm: *Settings → Tools → Python Integrated Tools → Default test runner:
pytest*, then right-click the `tests` folder → **Run 'pytest in tests'**.

## Roadmap

- [x] Stage 1: DOI normalization, Crossref + OpenAlex, retractions, `check` CLI.
- [x] Stage 2: citation contexts (Semantic Scholar), response cache, documentation.
- [ ] Local Retraction Watch database and retraction checks across the authors' other
  papers.
- [ ] PubPeer discussions.
- [ ] Traffic-light verdict `BLACK_CODE` / `RED` / `YELLOW` / `GREEN` with reasons.
- [ ] Classifying citation stance (supporting / contradicting).
- [ ] `papaver verify "<claim>"`: checking a claim against systematic reviews.
- [ ] Lookup by PMID and article URL.
