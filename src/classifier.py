"""Transparent, conservative vacancy classification rules."""

from __future__ import annotations

from typing import Any, Mapping

import config
from .interfaces import VacancyAnalyzer
from .parser import normalize_text


def thematic_score(text: str) -> tuple[int, str]:
    normalized = normalize_text(text)
    matches: list[tuple[str, int]] = []
    for term, weight in config.THEMATIC_WEIGHTS.items():
        if term in normalized:
            matches.append((term, weight))
    score = min(100, sum(weight for _, weight in matches))
    if not matches:
        return 0, "Nenhuma área temática prioritária foi identificada automaticamente."
    labels = [term.title() for term, _ in sorted(matches, key=lambda item: item[1], reverse=True)[:4]]
    level = "Alta" if score >= 75 else "Boa" if score >= 50 else "Aderência parcial"
    return score, f"{level} devido a: {', '.join(labels)}."


def classify_formal_eligibility(
    graduation_requirement: str | None = None,
    masters_requirement: str | None = None,
    doctorate_requirement: str | None = None,
    other_text: str | None = None,
) -> tuple[str, str]:
    """Classify textual requirements without claiming legal equivalence.

    Precedence matters: a clearly excluding undergraduate degree remains NO even
    when the doctorate is thematically compatible.
    """
    grad = normalize_text(graduation_requirement)
    masters = normalize_text(masters_requirement)
    doctorate = normalize_text(doctorate_requirement)
    whole = " ".join(part for part in (grad, masters, doctorate, normalize_text(other_text)) if part)

    if not whole:
        return "UNKNOWN", "O anúncio não informa requisitos de formação suficientes; consulte o edital oficial."

    # Explicit undergraduate exclusions.  The allow-list intentionally stays
    # narrow because false YES results are costlier than UNCERTAIN results.
    incompatible_grad = (
        "engenharia ambiental", "medicina", "enfermagem", "odontologia",
        "farmacia", "veterinaria", "direito", "arquitetura", "psicologia",
    )
    if grad and any(term in grad for term in incompatible_grad) and "areas afins" not in grad:
        return "NO", f"A graduação exigida parece exclusiva e incompatível: {graduation_requirement}"

    if grad and "administracao publica" in grad and "areas afins" not in grad:
        return "UNCERTAIN", (
            "O requisito cita especificamente Administração Pública; não se presume equivalência formal "
            "com Administração sem leitura do edital."
        )

    grad_compatible = bool(grad and "administracao" in grad)
    grad_unknown = bool(grad and not grad_compatible)

    # Never equate an interdisciplinary academic characteristic with CAPES's
    # distinct Interdisciplinary evaluation area.
    if "area de avaliacao interdisciplinar" in doctorate or "area interdisciplinar da capes" in doctorate:
        return "UNCERTAIN", (
            "O edital cita a Área de Avaliação Interdisciplinar da CAPES. O PPG-CiAC é da Área de "
            "Avaliação Ciências Ambientais; seu caráter interdisciplinar não cria equivalência automática."
        )
    if doctorate and ("doutorado em area interdisciplinar" in doctorate or "doutorado na area interdisciplinar" in doctorate):
        return "UNCERTAIN", (
            "A expressão 'área interdisciplinar' é ambígua. O perfil tem caráter interdisciplinar, "
            "mas Área de Avaliação CAPES Ciências Ambientais."
        )

    if "ciencias sociais aplicadas" in doctorate:
        return "UNCERTAIN", (
            "Ciências Ambientais pertence à Grande Área Multidisciplinar, não a Ciências Sociais Aplicadas; "
            "a aceitação depende da redação completa do edital."
        )

    if doctorate and "administracao" in doctorate:
        if "areas afins" in doctorate or "areas correlatas" in doctorate:
            return "UNCERTAIN", "Doutorado em Administração ou áreas afins exige interpretação humana da expressão 'áreas afins'."
        return "NO", "O edital parece exigir exclusivamente Doutorado em Administração, que não equivale automaticamente a Ciências Ambientais."

    uncertain_post_terms = (
        "desenvolvimento sustentavel", "sustentabilidade", "gestao ambiental",
        "desenvolvimento regional", "areas correlatas", "areas afins",
    )
    post = " ".join((masters, doctorate))
    if post and any(term in post for term in uncertain_post_terms) and "ciencias ambientais" not in post:
        return "UNCERTAIN", "A pós-graduação requerida usa uma área correlata ou a expressão 'áreas afins'; é necessária conferência humana."

    post_compatible = any(
        term in post
        for term in (
            "ciencias ambientais e conservacao", "ciencias ambientais",
            "grande area multidisciplinar", "area multidisciplinar",
        )
    )

    if grad_unknown:
        return "UNKNOWN", f"A graduação foi mencionada, mas a área não pôde ser comparada com segurança: {graduation_requirement}"
    if grad_compatible and (post_compatible or not post):
        return "YES", "A redação encontrada aceita Administração e a pós-graduação indicada é compatível ou não foi restringida."
    if not grad and post_compatible:
        return "YES", "A pós-graduação indicada é compatível; o anúncio não apresentou restrição adicional de graduação."
    if grad_compatible and post and not post_compatible:
        return "UNKNOWN", "A graduação parece compatível, mas a área da pós-graduação não foi identificada com segurança."
    return "UNKNOWN", "Os requisitos textuais são insuficientes para uma conclusão formal; consulte o edital oficial."


def geographic_priority(state: str | None) -> int:
    return config.GEOGRAPHIC_PRIORITIES.get((state or "").upper(), 4)


def visual_category(eligibility: str, score: int) -> str:
    if eligibility == "YES" and score >= config.STRONG_YES_SCORE:
        return "🔥 Forte oportunidade"
    if eligibility == "UNCERTAIN" and score >= config.STRONG_UNCERTAIN_SCORE:
        return "🔥 Forte oportunidade"
    if eligibility == "YES":
        return "🟢 Elegível / alta aderência" if score >= 50 else "🟢 Elegível"
    if eligibility == "UNCERTAIN":
        return "🟡 Elegibilidade incerta"
    if eligibility == "NO":
        return "🔴 Formalmente incompatível"
    return "⚪ Informação insuficiente"


class RuleBasedAnalyzer(VacancyAnalyzer):
    def analyze(self, vacancy: Mapping[str, Any], profile: Mapping[str, Any]) -> dict[str, Any]:
        del profile  # Rules are derived from config.PROFILE; parameter keeps the extension contract.
        eligibility, formal_reason = classify_formal_eligibility(
            vacancy.get("graduation_requirement_raw") or vacancy.get("graduation_requirement"),
            vacancy.get("masters_requirement_raw") or vacancy.get("masters_requirement"),
            vacancy.get("doctorate_requirement_raw") or vacancy.get("doctorate_requirement"),
            vacancy.get("other_requirements"),
        )
        thematic_input = " ".join(
            str(vacancy.get(key) or "")
            for key in (
                "title", "description", "position", "area", "subarea", "raw_text",
                "official_evidence_text",
            )
        )
        score, thematic_reason = thematic_score(thematic_input)
        return {
            "formal_eligibility": eligibility,
            "formal_reason": formal_reason,
            "thematic_score": score,
            "thematic_reason": thematic_reason,
            "geographic_priority": geographic_priority(vacancy.get("state")),
            "visual_category": visual_category(eligibility, score),
        }
