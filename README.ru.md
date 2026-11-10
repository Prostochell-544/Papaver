# Papaver

[English](README.md) | **Русский** | [Українська](README.uk.md)

**Papaver** — асинхронный CLI-аудитор научной целостности публикаций. По DOI статьи
он собирает из открытых библиографических баз всё, что нужно для быстрой проверки
due diligence: отозвана ли работа, были ли к ней исправления или «выражения
обеспокоенности» (expression of concern), где и кем она опубликована, и в каком
контексте её цитируют другие исследователи.

Типичный сценарий: перед тем как сослаться на статью или опереться на её выводы,
выполнить `papaver check <DOI>` и за несколько секунд увидеть, например, что
работа Уэйкфилда 1998 года о «связи» вакцины MMR с аутизмом отозвана журналом
The Lancet в 2010 году, а свежие цитирующие статьи упоминают её как пример
научного мошенничества.

## Возможности

Текущая версия (этап 2):

- **Метаданные из двух источников параллельно.** Crossref (регистратор DOI) и
  OpenAlex опрашиваются одновременно, их данные объединяются в одну строго
  типизированную модель `PaperMetadata`: название, год, журнал, ISSN, издатель,
  авторы с ORCID / OpenAlex ID и аффилиациями, статус открытого доступа,
  число цитирований.
- **Ретракции и редакционные уведомления.** Отзыв, исправление, expression of concern,
  withdrawal и т.д. берутся из поля Crossref `updated-by` (включая записи Retraction
  Watch, `source: retraction-watch`) и из флага OpenAlex `is_retracted`. Для каждого
  уведомления сохраняются тип, дата, DOI уведомления и источник.
- **Контексты цитирования из Semantic Scholar.** Предложения, в которых другие
  статьи ссылаются на проверяемую, вместе с намерением цитирования (`background`,
  `methodology`, `result`) и признаком «влиятельной» ссылки (`isInfluential`).
  Мусорные и пустые контексты отфильтровываются.
- **Локальный кэш** ответов API в SQLite (SQLAlchemy 2.0 async + aiosqlite) с TTL:
  повторная проверка той же статьи не отправляет запросов в сеть.
- **Устойчивость к сбоям.** Таймауты, повторы с экспоненциальной задержкой, учёт
  заголовка `Retry-After` при HTTP 429, ограничение частоты запросов к Semantic
  Scholar. Если один из источников недоступен, отчёт строится по остальным, а в
  нём появляется предупреждение о неполноте данных.
- **Два формата вывода:** читаемая сводка в терминале и полный отчёт в JSON
  (`--json`) для дальнейшей обработки.

В планах (см. [дорожную карту](#дорожная-карта)): обсуждения PubPeer, итоговый
вердикт-«светофор» (`BLACK_CODE` / `RED` / `YELLOW` / `GREEN`), репутация журнала
и авторов, команда `papaver verify "<утверждение>"`, поиск по PMID и URL.

## Требования

- Python **3.11+**
- Доступ в интернет (api.crossref.org, api.openalex.org, api.semanticscholar.org)

## Установка

### 1. Получите код

```bash
git clone https://github.com/Prostochell-544/Papaver.git
cd Papaver
```

Без git: скачайте архив репозитория (**Code → Download ZIP** на GitHub) и
распакуйте его.

### 2. Создайте и активируйте виртуальное окружение

Windows (PowerShell):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Если PowerShell запрещает запуск скриптов, используйте `cmd` и
`.venv\Scripts\activate.bat` или вызывайте интерпретатор напрямую:
`.venv\Scripts\python.exe`.

Linux / macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

**PyCharm:** *Settings → Project → Python Interpreter → Add Interpreter → Existing* и
укажите `.venv\Scripts\python.exe` (или `.venv/bin/python`).

### 3. Установите зависимости и сам пакет

```bash
pip install -r requirements.txt
pip install -e .
```

`pip install -e .` ставит пакет в режиме разработки: правки в `src/` подхватываются
без переустановки, а в окружении появляется команда `papaver`.

### 4. Настройте `.env`

Скопируйте шаблон:

```powershell
Copy-Item .env.example .env        # Windows PowerShell
```

```bash
cp .env.example .env               # Linux / macOS
```

и заполните значения. `.env` читается из **текущей рабочей директории**; переменные
окружения имеют приоритет над файлом. Файл `.env` исключён из git.

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `PAPAVER_CONTACT_EMAIL` | — | Ваш email. Подставляется в заголовок `User-Agent: Papaver/0.1.0 (mailto:...)`. Crossref и OpenAlex направляют такие запросы в **Polite Pool**: он быстрее и стабильнее анонимного. Настоятельно рекомендуется. |
| `PAPAVER_S2_API_KEY` | — | Ключ [Semantic Scholar API](https://www.semanticscholar.org/product/api) (бесплатно, по заявке). Без ключа запросы идут в общий пул, который часто отвечает HTTP 429; Papaver повторяет запросы, но с ключом заметно надёжнее. |
| `PAPAVER_DB_URL` | `sqlite+aiosqlite:///./papaver_cache.db` | База для кэша ответов. Относительный путь считается от рабочей директории. |
| `PAPAVER_CACHE_TTL_HOURS` | `24` | Сколько часов кэшированный ответ считается свежим. `0` — не читать из кэша. |
| `PAPAVER_S2_MAX_CITATIONS` | `1000` | Сколько цитирующих статей просматривать (максимум 9999: ограничение API). |
| `PAPAVER_HTTP_TIMEOUT` | `15` | Таймаут одного запроса, секунд. |
| `PAPAVER_MAX_RETRIES` | `2` | Повторы для Crossref / OpenAlex. |
| `PAPAVER_S2_MAX_RETRIES` | `4` | Повторы для Semantic Scholar. |
| `PAPAVER_S2_MIN_INTERVAL` | `1.0` | Минимальный интервал между запросами к Semantic Scholar, секунд. |

## Использование

```bash
papaver check "10.1016/S0140-6736(97)11096-0"
```

> **Берите DOI в кавычки.** Многие DOI содержат скобки, а их интерпретируют и
> PowerShell, и bash.

Принимаются любые распространённые формы записи DOI; все они приводятся к
каноническому виду `10.xxxx/...` в нижнем регистре:

```text
10.1016/S0140-6736(97)11096-0
doi:10.1016/S0140-6736(97)11096-0
https://doi.org/10.1016/S0140-6736(97)11096-0
https://doi.org/10.1016%2FS0140-6736%2897%2911096-0
https://onlinelibrary.wiley.com/doi/full/10.1002/anie.201915678
```

### Параметры `papaver check`

| Параметр | Описание |
|---|---|
| `--max-citations N` | Сколько цитирующих статей просмотреть в Semantic Scholar (0–9999, по умолчанию `PAPAVER_S2_MAX_CITATIONS`). `0` — не запрашивать цитирования. |
| `--contexts N` | Сколько контекстов цитирования вывести в сводке (по умолчанию 10). Сначала влиятельные, затем самые свежие. |
| `--json` | Вывести полный отчёт `AuditReport` в JSON вместо сводки. |
| `--no-cache` | Не читать и не обновлять локальный кэш. |

Глобальные параметры указываются **перед** командой:

```bash
papaver --version
papaver -v check "10.1016/S0140-6736(97)11096-0"   # логи HTTP-запросов, повторов и попаданий в кэш
```

### Пример: статья Уэйкфилда (1998)

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
        ...                       # ещё 12 авторов
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

Как это читать: `is_retracted=True`, а в `update_notices` есть уведомление
`RETRACTION` от 6 февраля 2010 года с DOI самого уведомления о ретракции. Блок
Semantic Scholar показывает, как статью цитируют сегодня.

### JSON-отчёт

```bash
papaver check "10.1016/S0140-6736(97)11096-0" --json > report.json
```

Структура верхнего уровня: `target`, `paper` (`PaperMetadata`), `citations`
(`items`, `scanned`, `truncated`), `warnings`, `verdict` и `reasons` (пока `null` и
пусто: заполнятся движком вердиктов), `generated_at`.

### Коды возврата

| Код | Значение |
|---|---|
| `0` | Отчёт построен (возможно, с предупреждениями о неполных данных). |
| `1` | DOI не найден ни в Crossref, ни в OpenAlex, либо источники недоступны. |
| `2` | Некорректный DOI или ошибка в настройках `PAPAVER_*`. |

### Запуск из PyCharm

`main.py` в корне проекта — обёртка над CLI. Создайте Run Configuration для
`main.py`, в поле **Parameters** укажите, например,
`check "10.1016/S0140-6736(97)11096-0"`, а **Working directory** оставьте корнем
проекта (оттуда читаются `.env` и кэш).

## Как это работает

```text
DOI ─► normalize_doi()
         │
         ├─► Crossref          /works/{doi}                  ─┐
         ├─► OpenAlex          /works/https://doi.org/{doi}  ─┴─► PaperMetadata ─┐
         └─► Semantic Scholar  /paper/DOI:{doi}/citations    ───► CitationScan  ─┴─► AuditReport
```

Все три источника опрашиваются одновременно (`asyncio`), и каждый запрос проходит
через SQLite-кэш ответов. Semantic Scholar читается страницами по 1000 записей, не
чаще одного запроса в секунду. Готовый `AuditReport` выводится в терминал или в
JSON. Метаданные обязательны: если DOI не найден ни в Crossref, ни в OpenAlex,
запрос к Semantic Scholar отменяется и команда завершается с ошибкой. Цитирования
необязательны: их недоступность превращается в предупреждение в отчёте.

### Ретракции: `updated-by` против `update-to`

Crossref хранит связь «уведомление → статья» с двух сторон:

- у **исправленной/отозванной статьи** есть поле `updated-by` со списком
  уведомлений о ней. Именно здесь лежит ретракция статьи Уэйкфилда;
- у **самого уведомления** есть поле `update-to`, указывающее на статью.

Papaver читает оба поля: `PaperMetadata.update_notices` — уведомления о
проверяемой статье, `PaperMetadata.amends` — заполняется, если проверяемый DOI сам
является уведомлением. У каждого `UpdateNotice` есть `notice_doi` и `target_doi`,
так что направление связи однозначно.

OpenAlex ставит `is_retracted: true` и самой записи-уведомлению о ретракции. Чтобы
уведомление не выглядело «отозванной статьёй», для таких записей (тип OpenAlex
`retraction`/`erratum` или наличие `update-to`) учитываются только данные Crossref.

### Контексты цитирования

- Semantic Scholar отдаёт не более 1000 записей за запрос и требует
  `offset + limit < 10000`, поэтому за один аудит можно просмотреть до 9999
  цитирующих статей.
- Значительная часть цитирующих статей приходит **без контекстов** (лицензионные
  ограничения издателей; у статьи Уэйкфилда примерно две трети). Такие статьи
  учитываются в счётчике `scanned`, но в `items` не попадают.
- Отбрасываются «мусорные» контексты: короче 20 символов или менее чем с 10 буквами
  (`[12]`, `(1–5)`, `...`), а также дубликаты внутри одной статьи (без учёта
  регистра и пробелов).

### Кэш

Ключ кэша — источник плюс полный URL запроса (включая параметры), значение —
сырой JSON-ответ. Кэшируются только успешные ответы, ошибки и 404 — никогда.
Сбой базы (например, файл заблокирован) не прерывает проверку: кэш просто
отключается с предупреждением. Чтобы сбросить кэш, удалите файл
`papaver_cache.db` или запустите проверку с `--no-cache`.

## Структура проекта

```text
Project_TrueFalse/
├── main.py                  # точка входа для PyCharm → papaver.cli:app
├── pyproject.toml           # метаданные пакета, entry point `papaver`, настройки pytest
├── README.md                # на английском; README.ru.md, README.uk.md — переводы
├── requirements.txt
├── .env.example             # шаблон настроек
├── src/papaver/
│   ├── cli.py               # Typer: команда `check`, коды возврата, UTF-8 вывод
│   ├── core/
│   │   ├── config.py        # Settings (pydantic-settings), User-Agent для Polite Pool
│   │   ├── schemas.py       # Pydantic v2: PaperMetadata, UpdateNotice, CitationScan, AuditReport…
│   │   └── exceptions.py    # PaperNotFoundError, UpstreamTimeoutError, UpstreamServiceError
│   ├── services/
│   │   ├── http.py          # базовый клиент: повторы, Retry-After, rate limit, кэш
│   │   ├── ingest.py        # Crossref + OpenAlex → PaperMetadata
│   │   ├── citations.py     # Semantic Scholar → CitationContextItem / CitationScan
│   │   └── audit.py         # конвейер `check`: всё вместе → AuditReport
│   ├── db/
│   │   ├── models.py        # SQLAlchemy 2.0: таблица api_cache
│   │   ├── session.py       # AsyncEngine, async_sessionmaker
│   │   └── cache.py         # ResponseCache с TTL
│   └── utils/
│       ├── doi.py           # нормализация DOI (без зависимостей, совместима с Alcedo)
│       ├── payload.py       # безопасный доступ к полям «сырого» JSON
│       └── formatters.py    # вывод отчёта в терминал (rich)
└── tests/                   # pytest + respx, без обращений к сети
```

Модели в `core/schemas.py` строгие (`extra="forbid"`, нормализация и проверка DOI,
ORCID, ISSN, OpenAlex ID), а очистка «грязных» ответов API происходит в сервисах.
Поэтому одно некорректное поле в ответе API не роняет весь отчёт.

## Тесты

```bash
python -m pytest
```

- Тесты **не ходят в сеть**: HTTP-запросы перехватываются через
  [respx](https://lundberg.github.io/respx/), а фикстуры (`tests/conftest.py`) —
  урезанные копии реальных ответов Crossref, OpenAlex и Semantic Scholar.
- Асинхронные тесты работают через `pytest-asyncio` в режиме `asyncio_mode = "auto"`
  (настроено в `pyproject.toml`), декораторы не нужны.
- Тесты не читают ваш `.env` и используют временную базу кэша.

Полезные варианты:

```bash
python -m pytest -v                          # подробный список тестов
python -m pytest tests/test_citations.py     # один модуль
python -m pytest -k "429 or timeout"         # по подстроке в имени теста
```

| Модуль | Что проверяет |
|---|---|
| `test_doi.py` | Нормализация DOI: префиксы, URL, percent-encoding, скобки, мусор. |
| `test_ingest.py` | Слияние Crossref + OpenAlex, ретракции, фолбэк при отказе источника, 404, таймауты, повторы. |
| `test_citations.py` | Разбор и фильтрация контекстов, пагинация, лимит `offset + limit`, API-ключ, 429 / `Retry-After`. |
| `test_cache.py` | Кэш: чтение/запись, TTL, отказоустойчивость, отсутствие повторных HTTP-запросов. |
| `test_audit.py` | Конвейер `check` целиком и CLI: вывод, `--json`, коды возврата. |

В PyCharm: *Settings → Tools → Python Integrated Tools → Default test runner:
pytest*, затем правый клик по папке `tests` → **Run 'pytest in tests'**.

## Дорожная карта

- [x] Этап 1: нормализация DOI, Crossref + OpenAlex, ретракции, CLI `check`.
- [x] Этап 2: контексты цитирования (Semantic Scholar), кэш ответов, документация.
- [ ] Локальная база Retraction Watch и проверка ретракций у других работ авторов.
- [ ] Обсуждения на PubPeer.
- [ ] Вердикт-«светофор» `BLACK_CODE` / `RED` / `YELLOW` / `GREEN` с обоснованием.
- [ ] Классификация тональности цитирований (поддерживающие / опровергающие).
- [ ] `papaver verify "<утверждение>"`: проверка утверждения по систематическим обзорам.
- [ ] Поиск по PMID и URL статьи.
