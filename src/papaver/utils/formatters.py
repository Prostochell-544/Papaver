"""Terminal rendering of audit reports using Rich."""

from collections import Counter

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from papaver.core.schemas import AuditReport, CitationContextItem, CitationScan, VerdictLevel
from papaver.core.verdict import analyze_citation_sentiment, compute_verdict

__all__ = ["render_audit_report", "select_contexts"]

_MAX_CONTEXT_CHARS = 600

VERDICT_STYLES = {
    VerdictLevel.BLACK_CODE: {
        "title": "🚨 [КОД ЧЁРНЫЙ: ОТОЗВАНО / ФАЛЬСИФИКАЦИЯ]",
        "border": "bold white on red",
        "badge": "[bold white on red] BLACK CODE [/]",
    },
    VerdictLevel.RED: {
        "title": "🔴 [КОД КРАСНЫЙ: ВЫСОКИЙ РИСК]",
        "border": "bold red",
        "badge": "[bold white on bright_red] RED [/]",
    },
    VerdictLevel.YELLOW: {
        "title": "🟡 [КОД ЖЁЛТЫЙ: ТРЕБУЕТ ВНИМАНИЯ]",
        "border": "bold yellow",
        "badge": "[bold black on yellow] YELLOW [/]",
    },
    VerdictLevel.GREEN: {
        "title": "🟢 [КОД ЗЕЛЁНЫЙ: ЧИСТАЯ ПУБЛИКАЦИЯ]",
        "border": "bold green",
        "badge": "[bold white on green] GREEN [/]",
    },
}


def select_contexts(scan: CitationScan, limit: int) -> list[CitationContextItem]:
    """Influential citations first, then the most recent ones."""
    ranked = sorted(
        scan.items,
        key=lambda item: (not item.is_influential, -(item.citing_paper.year or 0)),
    )
    return ranked[:limit]


def render_audit_report(
    report: AuditReport,
    console: Console,
    *,
    max_contexts: int = 10,
) -> None:
    """Print the full visual audit report with verdict banners, metadata, and critique."""
    meta = report.paper
    citations = report.citations
    level, summary, flags = compute_verdict(meta, citations, report.warnings)
    v_style = VERDICT_STYLES[level]

    console.print()

    # 1. Заглавный баннер с вердиктом
    banner_content = Text.assemble(
        (f"{summary}\n", "bold"),
        ("DOI: ", "dim"),
        (f"{meta.doi}\n", "bold cyan"),
        ("Название: ", "dim"),
        (f"{meta.title}\n", "white"),
        ("Журнал: ", "dim"),
        (f"{meta.journal_name or 'Не указан'} ({meta.year or 'Год не указан'})", "italic yellow"),
    )
    console.print(
        Panel(
            banner_content,
            title=v_style["title"],
            border_style=v_style["border"],
            expand=True,
            padding=(1, 2),
        )
    )

    # 2. Факторы оценки целостности
    flag_table = Table(box=None, show_header=False, pad_edge=False, padding=(0, 1))
    flag_table.add_column(
        "Bullet",
        style="bold red" if level in (VerdictLevel.BLACK_CODE, VerdictLevel.RED) else "bold yellow",
    )
    flag_table.add_column("Description")

    for f in flags:
        flag_table.add_row("•", f)

    console.print(
        Panel(
            flag_table,
            title="[bold]Факторы оценки целостности[/bold]",
            border_style="dim",
            expand=True,
        )
    )

    # 3. Авторы и метаданные публикации
    author_table = Table(
        title="Информация об авторах и охвате",
        header_style="bold magenta",
        expand=True,
        box=None,
    )
    author_table.add_column("Первые авторы", style="white")
    author_table.add_column("Цитирований (OpenAlex)", justify="right", style="cyan")
    author_table.add_column("Open Access", justify="center")
    author_table.add_column("Источники", style="dim")

    authors_str = ", ".join(a.name for a in meta.authors[:4])
    if len(meta.authors) > 4:
        authors_str += f" et al. (всего {len(meta.authors)})"

    author_table.add_row(
        authors_str or "Неизвестно",
        str(meta.cited_by_count or 0),
        "🟢 Да" if meta.is_oa else "⚪ Нет",
        ", ".join(s.value for s in meta.sources),
    )
    console.print(Panel(author_table, border_style="dim", expand=True))

    # 4. Анализ цитирований и ключевые критические цитаты
    if citations and citations.items:
        crit_count, total_ctx, crit_hits = analyze_citation_sentiment(citations)
        more = ", more available" if citations.truncated else ""

        cit_summary = (
            f"Просканировано статей: [bold]{citations.scanned}[/]{more} | "
            f"С доступными контекстами: [bold]{len(citations.items)}[/] | "
            f"Критических упоминаний: [bold {'red' if crit_count > 0 else 'green'}]{crit_count}[/]"
        )
        console.print(
            Panel(
                cit_summary,
                title="[bold]Профиль цитирования (Semantic Scholar)[/bold]",
                border_style="dim",
            )
        )

        if crit_hits:
            crit_table = Table(
                title="🚨 Выдержки из критикующих и опровергающих работ",
                header_style="bold red",
                border_style="red dim",
                expand=True,
            )
            crit_table.add_column("Источник", style="cyan", width=28)
            crit_table.add_column("Контекст цитирования", style="white")

            for item, quote in crit_hits[:max_contexts]:
                paper = item.citing_paper
                paper_label = (
                    f"{paper.year or 'N/A'} - {paper.title[:35]}...\n"
                    f"[dim]DOI: {paper.doi or 'N/A'}[/dim]"
                )
                if len(quote) > _MAX_CONTEXT_CHARS:
                    quote = quote[: _MAX_CONTEXT_CHARS - 3].rstrip() + "..."
                crit_table.add_row(paper_label, f"«{quote.strip()}»")

            console.print(crit_table)
    elif citations is None:
        console.print(
            Panel(
                Text("Данные цитирования недоступны (см. предупреждения ниже).", style="dim"),
                title="Профиль цитирования",
                border_style="dim",
            )
        )

    # 5. Предупреждения системы
    if report.warnings:
        warn_text = Text("\n".join(f"⚠️  {w}" for w in report.warnings), style="yellow dim")
        console.print(Panel(warn_text, title="Предупреждения аудита", border_style="yellow dim"))

    console.print()