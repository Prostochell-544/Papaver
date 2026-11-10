"""Verdict and scoring engine for scientific publication integrity."""

import re
from typing import Sequence

from papaver.core.schemas import (
    AuditReport,
    CitationContextItem,
    CitationScan,
    PaperMetadata,
    UpdateNotice,
    VerdictLevel,
)

# Сигнальные паттерны для выявления критики в контекстах цитирования
CRITIQUE_PATTERNS = [
    r"\b(retract(ed|ion)?|withdrawn)\b",
    r"\b(fabricat(ed|ion)|falsif(ied|ication)|fraud(ulent)?)\b",
    r"\b(flawed|misleading|spurious|invalid|erroneous)\b",
    r"\b(failed to replicate|inability to replicate|not replicable)\b",
    r"\b(unsubstantiated|discredited|refut(ed|ing)|debunk(ed)?)\b",
    r"\b(disproven|unreliable|ethical violation|conflict of interest)\b",
]
CRITIQUE_REGEX = re.compile("|".join(CRITIQUE_PATTERNS), re.IGNORECASE)


def analyze_citation_sentiment(
        citations: CitationScan | None,
) -> tuple[int, int, list[tuple[CitationContextItem, str]]]:
    """Анализирует контексты цитат на предмет критики и опровержений.

    Возвращает (кол-во критических, всего проанализировано контекстов, список критических цитат с текстом).
    """
    if not citations or not citations.items:
        return 0, 0, []

    critique_hits: list[tuple[CitationContextItem, str]] = []
    total_contexts = 0

    for item in citations.items:
        for ctx in item.contexts:
            total_contexts += 1
            if CRITIQUE_REGEX.search(ctx):
                critique_hits.append((item, ctx))
                break  # Одна критическая цитата на статью для баланса

    return len(critique_hits), total_contexts, critique_hits


def compute_verdict(
        metadata: PaperMetadata,
        citations: CitationScan | None = None,
        warnings: Sequence[str] | None = None,
) -> tuple[VerdictLevel, str, list[str]]:
    """Вычисляет уровень вердикта (светофор) и формирует перечень причин.

    Возвращает (VerdictLevel, summary_reason, list_of_flag_details).
    """
    flags: list[str] = []

    # 1. Проверка на BLACK_CODE (Официальный отзыв / Фальсификация)
    has_retraction_notice = any(
        notice.update_type.lower() in ("retraction", "withdrawal")
        for notice in metadata.update_notices
    )

    if metadata.is_retracted or has_retraction_notice:
        flags.append("Статья официально ОТОЗВАНА (Retracted) издателем или базой данных.")
        for notice in metadata.update_notices:
            if notice.update_type.lower() in ("retraction", "withdrawal"):
                flags.append(
                    f"Уведомление об отзыве: DOI {notice.notice_doi or 'N/A'} (источник: {notice.source})"
                )
        return (
            VerdictLevel.BLACK_CODE,
            "СТАТЬЯ ОТОЗВАНА / ПУБЛИКАЦИЯ АННУЛИРОВАНА",
            flags,
        )

    # 2. Анализ цитирований и критики
    critique_count, total_contexts, _ = analyze_citation_sentiment(citations)
    critique_ratio = (critique_count / total_contexts) if total_contexts > 0 else 0.0

    # 3. Проверка на RED (Серьёзные сомнения, Expression of Concern, разгром в литературе)
    has_expression_of_concern = any(
        "concern" in notice.update_type.lower() for notice in metadata.update_notices
    )
    if has_expression_of_concern:
        flags.append("Редакция выпустила официальное предупреждение (Expression of Concern).")

    if total_contexts >= 5 and critique_ratio >= 0.25:
        flags.append(
            f"Аномально высокая доля критических цитирований: {critique_ratio:.1%} "
            f"({critique_count} из {total_contexts} контекстов содержат указания на ошибки/опровержение)."
        )

    if has_expression_of_concern or (total_contexts >= 5 and critique_ratio >= 0.35):
        return (
            VerdictLevel.RED,
            "ВЫСОКИЙ РИСК: НАЛИЧИЕ ОФИЦИАЛЬНЫХ ПРЕДУПРЕЖДЕНИЙ ИЛИ МАССОВАЯ КРИТИКА",
            flags,
        )

    # 4. Проверка на YELLOW (Коррекции, препринты, умеренная критика)
    has_corrections = any(
        "correction" in notice.update_type.lower() or "erratum" in notice.update_type.lower()
        for notice in metadata.update_notices
    )
    if has_corrections:
        flags.append("К статье публиковались исправления или эррата (Correction/Erratum).")

    if critique_count > 0:
        flags.append(
            f"Обнаружена критика в цитирующей литературе ({critique_count} упоминаний сомнений/невоспроизводимости)."
        )

    if metadata.journal_name and any(
            p in metadata.journal_name.lower() for p in ("biorxiv", "medrxiv", "arxiv", "preprint")
    ):
        flags.append("Публикация является нерецензированным препринтом.")

    if flags or (total_contexts >= 5 and critique_ratio >= 0.10):
        return (
            VerdictLevel.YELLOW,
            "ТРЕБУЕТ ОСТОРОЖНОСТИ: ПРИСУТСТВУЮТ ЗАМЕЧАНИЯ ИЛИ КОРРЕКЦИИ",
            flags,
        )

    # 5. GREEN (Чистая репутация)
    flags.append("Официальных ретракций и предупреждений не обнаружено.")
    if citations and citations.scanned > 0:
        flags.append(f"Просканировано {citations.scanned} цитирований, прямых сигналов фрода не выявлено.")
    return (
        VerdictLevel.GREEN,
        "НАДЁЖНЫЙ ИСТОЧНИК: НЕТ ПРИЗНАКОВ ОТЗЫВА ИЛИ АНОМАЛЬНОЙ КРИТИКИ",
        flags,
    )