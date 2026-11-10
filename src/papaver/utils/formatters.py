"""Terminal rendering of audit reports.

Intentionally plain: this is the stable interface (``render_audit_report``) that a
richer report design can replace without touching the pipeline.
"""

from collections import Counter

from rich.console import Console
from rich.rule import Rule
from rich.text import Text

from papaver.core.schemas import AuditReport, CitationContextItem, CitationScan

__all__ = ["render_audit_report", "select_contexts"]

_MAX_CONTEXT_CHARS = 600


def render_audit_report(report: AuditReport, console: Console, *, max_contexts: int = 10) -> None:
    """Print the merged metadata, a citation-context digest and any warnings."""
    console.print(report.paper)
    console.print()
    console.print(Rule("Citation contexts (Semantic Scholar)", style="dim"))
    if report.citations is None:
        console.print(Text("Not available (see warnings below).", style="dim"))
    else:
        _render_citations(report.citations, console, max_contexts)
    for warning in report.warnings:
        console.print(Text.assemble(("Warning: ", "bold yellow"), warning))


def select_contexts(scan: CitationScan, limit: int) -> list[CitationContextItem]:
    """Influential citations first, then the most recent ones."""
    ranked = sorted(
        scan.items,
        key=lambda item: (not item.is_influential, -(item.citing_paper.year or 0)),
    )
    return ranked[:limit]


def _render_citations(scan: CitationScan, console: Console, max_contexts: int) -> None:
    more = ", more available" if scan.truncated else ""
    console.print(
        f"Scanned {scan.scanned} citing papers{more}: {len(scan.items)} with usable contexts, "
        f"{len(scan.influential)} influential."
    )
    intents = Counter(intent.value for item in scan.items for intent in item.intents)
    if intents:
        console.print("Intents: " + ", ".join(f"{name} {count}" for name, count in intents.most_common()))

    selected = select_contexts(scan, max_contexts)
    if not selected:
        return
    console.print()
    for item in selected:
        console.print(_format_item(item))


def _format_item(item: CitationContextItem) -> Text:
    paper = item.citing_paper
    line = Text()
    line.append(f"{paper.year or '----'}  ", style="bold")
    line.append(paper.title or "(untitled)", style="bold")
    details = [f"doi:{paper.doi}" if paper.doi else None, ", ".join(i.value for i in item.intents) or None]
    if item.is_influential:
        details.append("influential")
    meta = " | ".join(d for d in details if d)
    if meta:
        line.append(f"\n      {meta}", style="dim")
    context = item.contexts[0]
    if len(context) > _MAX_CONTEXT_CHARS:
        context = context[: _MAX_CONTEXT_CHARS - 3].rstrip() + "..."
    line.append(f'\n      "{context}"')
    if len(item.contexts) > 1:
        line.append(f"\n      (+{len(item.contexts) - 1} more)", style="dim")
    line.append("\n")
    return line
