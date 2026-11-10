# Papaver

[English](README.md) | [Русский](README.ru.md) | **Українська**

**Papaver** — асинхронний CLI-аудитор наукової доброчесності публікацій. За DOI
статті він збирає з відкритих бібліографічних баз усе, що потрібно для швидкої
перевірки due diligence: чи відкликано роботу, чи були до неї виправлення або
«висловлення занепокоєння» (expression of concern), де й ким її опубліковано та в
якому контексті її цитують інші дослідники.

Типовий сценарій: перш ніж посилатися на статтю чи спиратися на її висновки,
виконати `papaver check <DOI>` і за кілька секунд побачити, наприклад, що роботу
Вейкфілда 1998 року про «зв'язок» вакцини MMR з аутизмом журнал The Lancet відкликав
у 2010 році, а свіжі статті, які її цитують, згадують її як приклад наукового
шахрайства.

## Можливості

Поточна версія (етап 2):

- **Метадані з двох джерел паралельно.** Crossref (реєстратор DOI) та OpenAlex
  опитуються одночасно, їхні дані об'єднуються в одну строго типізовану модель
  `PaperMetadata`: назва, рік, журнал, ISSN, видавець, автори з ORCID / OpenAlex ID
  та афіліаціями, статус відкритого доступу, кількість цитувань.
- **Ретракції та редакційні повідомлення.** Відкликання, виправлення, expression of
  concern, withdrawal тощо беруться з поля Crossref `updated-by` (зокрема записи
  Retraction Watch, `source: retraction-watch`) та з прапорця OpenAlex
  `is_retracted`. Для кожного повідомлення зберігаються тип, дата, DOI повідомлення
  та джерело.
- **Контексти цитування з Semantic Scholar.** Речення, в яких інші статті
  посилаються на перевірювану, разом із наміром цитування (`background`,
  `methodology`, `result`) та ознакою «впливового» посилання (`isInfluential`).
  Сміттєві та порожні контексти відфільтровуються.
- **Локальний кеш** відповідей API в SQLite (SQLAlchemy 2.0 async + aiosqlite) з
  TTL: повторна перевірка тієї самої статті не надсилає запитів у мережу.
- **Стійкість до збоїв.** Тайм-аути, повтори з експоненційною затримкою, урахування
  заголовка `Retry-After` при HTTP 429, обмеження частоти запитів до Semantic
  Scholar. Якщо одне з джерел недоступне, звіт будується за рештою, а в ньому
  з'являється попередження про неповноту даних.
- **Два формати виводу:** зручне для читання зведення в терміналі та повний звіт у
  JSON (`--json`) для подальшої обробки.

У планах (див. [дорожню карту](#дорожня-карта)): обговорення PubPeer, підсумковий
вердикт-«світлофор» (`BLACK_CODE` / `RED` / `YELLOW` / `GREEN`), репутація журналу
та авторів, команда `papaver verify "<твердження>"`, пошук за PMID та URL.

## Вимоги

- Python **3.11+**
- Доступ до інтернету (api.crossref.org, api.openalex.org, api.semanticscholar.org)

## Встановлення

### 1. Отримайте код

```bash
git clone https://github.com/Prostochell-544/Papaver.git
cd Papaver
```

Без git: завантажте архів репозиторію (**Code → Download ZIP** на GitHub) і
розпакуйте його.

### 2. Створіть та активуйте віртуальне середовище

Windows (PowerShell):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Якщо PowerShell забороняє запуск скриптів, використовуйте `cmd` і
`.venv\Scripts\activate.bat` або викликайте інтерпретатор напряму:
`.venv\Scripts\python.exe`.

Linux / macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

**PyCharm:** *Settings → Project → Python Interpreter → Add Interpreter → Existing* і
вкажіть `.venv\Scripts\python.exe` (або `.venv/bin/python`).

### 3. Встановіть залежності та сам пакет

```bash
pip install -r requirements.txt
pip install -e .
```

`pip install -e .` встановлює пакет у режимі розробки: зміни в `src/` підхоплюються
без перевстановлення, а в середовищі з'являється команда `papaver`.

### 4. Налаштуйте `.env`

Скопіюйте шаблон:

```powershell
Copy-Item .env.example .env        # Windows PowerShell
```

```bash
cp .env.example .env               # Linux / macOS
```

і заповніть значення. `.env` читається з **поточної робочої директорії**; змінні
середовища мають пріоритет над файлом. Файл `.env` виключено з git.

| Змінна | За замовчуванням | Призначення |
|---|---|---|
| `PAPAVER_CONTACT_EMAIL` | — | Ваш email. Підставляється в заголовок `User-Agent: Papaver/0.1.0 (mailto:...)`. Crossref та OpenAlex спрямовують такі запити до **Polite Pool**: він швидший і стабільніший за анонімний. Наполегливо рекомендується. |
| `PAPAVER_S2_API_KEY` | — | Ключ [Semantic Scholar API](https://www.semanticscholar.org/product/api) (безкоштовно, за заявкою). Без ключа запити йдуть до спільного пулу, який часто відповідає HTTP 429; Papaver повторює запити, але з ключем помітно надійніше. |
| `PAPAVER_DB_URL` | `sqlite+aiosqlite:///./papaver_cache.db` | База для кешу відповідей. Відносний шлях рахується від робочої директорії. |
| `PAPAVER_CACHE_TTL_HOURS` | `24` | Скільки годин кешована відповідь вважається свіжою. `0` — не читати з кешу. |
| `PAPAVER_S2_MAX_CITATIONS` | `1000` | Скільки статей, що цитують, переглядати (максимум 9999: обмеження API). |
| `PAPAVER_HTTP_TIMEOUT` | `15` | Тайм-аут одного запиту, секунд. |
| `PAPAVER_MAX_RETRIES` | `2` | Повтори для Crossref / OpenAlex. |
| `PAPAVER_S2_MAX_RETRIES` | `4` | Повтори для Semantic Scholar. |
| `PAPAVER_S2_MIN_INTERVAL` | `1.0` | Мінімальний інтервал між запитами до Semantic Scholar, секунд. |

## Використання

```bash
papaver check "10.1016/S0140-6736(97)11096-0"
```

> **Беріть DOI в лапки.** Багато DOI містять дужки, а їх інтерпретують і
> PowerShell, і bash.

Приймаються будь-які поширені форми запису DOI; усі вони зводяться до канонічного
вигляду `10.xxxx/...` у нижньому регістрі:

```text
10.1016/S0140-6736(97)11096-0
doi:10.1016/S0140-6736(97)11096-0
https://doi.org/10.1016/S0140-6736(97)11096-0
https://doi.org/10.1016%2FS0140-6736%2897%2911096-0
https://onlinelibrary.wiley.com/doi/full/10.1002/anie.201915678
```

### Параметри `papaver check`

| Параметр | Опис |
|---|---|
| `--max-citations N` | Скільки статей, що цитують, переглянути в Semantic Scholar (0–9999, за замовчуванням `PAPAVER_S2_MAX_CITATIONS`). `0` — не запитувати цитування. |
| `--contexts N` | Скільки контекстів цитування вивести у зведенні (за замовчуванням 10). Спочатку впливові, потім найсвіжіші. |
| `--json` | Вивести повний звіт `AuditReport` у JSON замість зведення. |
| `--no-cache` | Не читати й не оновлювати локальний кеш. |

Глобальні параметри вказуються **перед** командою:

```bash
papaver --version
papaver -v check "10.1016/S0140-6736(97)11096-0"   # логи HTTP-запитів, повторів і влучань у кеш
```

### Приклад: стаття Вейкфілда (1998)

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
        ...                       # ще 12 авторів
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

Як це читати: `is_retracted=True`, а в `update_notices` є повідомлення `RETRACTION`
від 6 лютого 2010 року з DOI самого повідомлення про ретракцію. Блок Semantic
Scholar показує, як статтю цитують сьогодні.

### JSON-звіт

```bash
papaver check "10.1016/S0140-6736(97)11096-0" --json > report.json
```

Структура верхнього рівня: `target`, `paper` (`PaperMetadata`), `citations`
(`items`, `scanned`, `truncated`), `warnings`, `verdict` і `reasons` (поки що `null`
і порожньо: їх заповнить рушій вердиктів), `generated_at`.

### Коди повернення

| Код | Значення |
|---|---|
| `0` | Звіт побудовано (можливо, з попередженнями про неповні дані). |
| `1` | DOI не знайдено ні в Crossref, ні в OpenAlex, або джерела недоступні. |
| `2` | Некоректний DOI або помилка в налаштуваннях `PAPAVER_*`. |

### Запуск із PyCharm

`main.py` у корені проєкту — обгортка над CLI. Створіть Run Configuration для
`main.py`, у полі **Parameters** вкажіть, наприклад,
`check "10.1016/S0140-6736(97)11096-0"`, а **Working directory** залиште коренем
проєкту (звідти читаються `.env` і кеш).

## Як це працює

```text
DOI ─► normalize_doi()
         │
         ├─► Crossref          /works/{doi}                  ─┐
         ├─► OpenAlex          /works/https://doi.org/{doi}  ─┴─► PaperMetadata ─┐
         └─► Semantic Scholar  /paper/DOI:{doi}/citations    ───► CitationScan  ─┴─► AuditReport
```

Усі три джерела опитуються одночасно (`asyncio`), і кожен запит проходить через
SQLite-кеш відповідей. Semantic Scholar читається сторінками по 1000 записів, не
частіше ніж один запит на секунду. Готовий `AuditReport` виводиться в термінал або в
JSON. Метадані обов'язкові: якщо DOI не знайдено ні в Crossref, ні в OpenAlex,
запит до Semantic Scholar скасовується, і команда завершується з помилкою.
Цитування необов'язкові: якщо вони недоступні, у звіті з'являється попередження.

### Ретракції: `updated-by` проти `update-to`

Crossref зберігає зв'язок «повідомлення → стаття» з двох боків:

- у **виправленої чи відкликаної статті** є поле `updated-by` зі списком повідомлень
  про неї. Саме тут записано ретракцію статті Вейкфілда;
- у **самого повідомлення** є поле `update-to`, що вказує на статтю.

Papaver читає обидва поля: `PaperMetadata.update_notices` — повідомлення про
перевірювану статтю, `PaperMetadata.amends` — заповнюється, якщо перевірюваний DOI
сам є повідомленням. Кожен `UpdateNotice` має `notice_doi` і `target_doi`, тож
напрям зв'язку однозначний.

OpenAlex ставить `is_retracted: true` і самому запису-повідомленню про ретракцію.
Щоб повідомлення не виглядало «відкликаною статтею», для таких записів (тип OpenAlex
`retraction`/`erratum` або наявність `update-to`) враховуються лише дані Crossref.

### Контексти цитування

- Semantic Scholar віддає не більше 1000 записів за запит і вимагає
  `offset + limit < 10000`, тому за один аудит можна переглянути до 9999 статей, що
  цитують.
- Значна частина статей, що цитують, надходить **без контекстів** (ліцензійні
  обмеження видавців; у статті Вейкфілда приблизно дві третини). Такі статті
  враховуються в лічильнику `scanned`, але до `items` не потрапляють.
- Відкидаються «сміттєві» контексти: коротші за 20 символів або з менш ніж 10
  літерами (`[12]`, `(1–5)`, `...`), а також дублікати в межах однієї статті (без
  урахування регістру та пробілів).

### Кеш

Ключ кешу — джерело плюс повний URL запиту (разом із параметрами), значення — сира
JSON-відповідь. Кешуються лише успішні відповіді, помилки та 404 — ніколи. Збій бази
(наприклад, файл заблоковано) не перериває перевірку: кеш просто вимикається з
попередженням. Щоб скинути кеш, видаліть файл `papaver_cache.db` або запустіть
перевірку з `--no-cache`.

## Структура проєкту

```text
Project_TrueFalse/
├── main.py                  # точка входу для PyCharm → papaver.cli:app
├── pyproject.toml           # метадані пакета, entry point `papaver`, налаштування pytest
├── README.md                # англійською; README.ru.md, README.uk.md — переклади
├── requirements.txt
├── .env.example             # шаблон налаштувань
├── src/papaver/
│   ├── cli.py               # Typer: команда `check`, коди повернення, UTF-8 вивід
│   ├── core/
│   │   ├── config.py        # Settings (pydantic-settings), User-Agent для Polite Pool
│   │   ├── schemas.py       # Pydantic v2: PaperMetadata, UpdateNotice, CitationScan, AuditReport…
│   │   └── exceptions.py    # PaperNotFoundError, UpstreamTimeoutError, UpstreamServiceError
│   ├── services/
│   │   ├── http.py          # базовий клієнт: повтори, Retry-After, rate limit, кеш
│   │   ├── ingest.py        # Crossref + OpenAlex → PaperMetadata
│   │   ├── citations.py     # Semantic Scholar → CitationContextItem / CitationScan
│   │   └── audit.py         # конвеєр `check`: усе разом → AuditReport
│   ├── db/
│   │   ├── models.py        # SQLAlchemy 2.0: таблиця api_cache
│   │   ├── session.py       # AsyncEngine, async_sessionmaker
│   │   └── cache.py         # ResponseCache з TTL
│   └── utils/
│       ├── doi.py           # нормалізація DOI (без залежностей, сумісна з Alcedo)
│       ├── payload.py       # безпечний доступ до полів «сирого» JSON
│       └── formatters.py    # вивід звіту в термінал (rich)
└── tests/                   # pytest + respx, без звернень до мережі
```

Моделі в `core/schemas.py` строгі (`extra="forbid"`, нормалізація та перевірка DOI,
ORCID, ISSN, OpenAlex ID), а очищення «брудних» відповідей API відбувається в
сервісах. Тому одне некоректне поле у відповіді API не ламає весь звіт.

## Тести

```bash
python -m pytest
```

- Тести **не звертаються до мережі**: HTTP-запити перехоплюються через
  [respx](https://lundberg.github.io/respx/), а фікстури (`tests/conftest.py`) —
  урізані копії реальних відповідей Crossref, OpenAlex і Semantic Scholar.
- Асинхронні тести працюють через `pytest-asyncio` у режимі `asyncio_mode = "auto"`
  (налаштовано в `pyproject.toml`), декоратори не потрібні.
- Тести не читають ваш `.env` і використовують тимчасову базу кешу.

Корисні варіанти:

```bash
python -m pytest -v                          # докладний список тестів
python -m pytest tests/test_citations.py     # один модуль
python -m pytest -k "429 or timeout"         # за підрядком в імені тесту
```

| Модуль | Що перевіряє |
|---|---|
| `test_doi.py` | Нормалізація DOI: префікси, URL, percent-encoding, дужки, сміття. |
| `test_ingest.py` | Злиття Crossref + OpenAlex, ретракції, запасний варіант при відмові джерела, 404, тайм-аути, повтори. |
| `test_citations.py` | Розбір і фільтрація контекстів, пагінація, ліміт `offset + limit`, API-ключ, 429 / `Retry-After`. |
| `test_cache.py` | Кеш: читання/запис, TTL, відмовостійкість, відсутність повторних HTTP-запитів. |
| `test_audit.py` | Конвеєр `check` повністю і CLI: вивід, `--json`, коди повернення. |

У PyCharm: *Settings → Tools → Python Integrated Tools → Default test runner:
pytest*, потім клацніть правою кнопкою на теці `tests` → **Run 'pytest in tests'**.

## Дорожня карта

- [x] Етап 1: нормалізація DOI, Crossref + OpenAlex, ретракції, CLI `check`.
- [x] Етап 2: контексти цитування (Semantic Scholar), кеш відповідей, документація.
- [ ] Локальна база Retraction Watch і перевірка ретракцій в інших роботах авторів.
- [ ] Обговорення на PubPeer.
- [ ] Вердикт-«світлофор» `BLACK_CODE` / `RED` / `YELLOW` / `GREEN` з обґрунтуванням.
- [ ] Класифікація тональності цитувань (підтримувальні / спростовувальні).
- [ ] `papaver verify "<твердження>"`: перевірка твердження за систематичними оглядами.
- [ ] Пошук за PMID та URL статті.
