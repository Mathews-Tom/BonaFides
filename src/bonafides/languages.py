# SPDX-License-Identifier: MIT OR Apache-2.0
"""Closed display vocabulary; owner-written phrases are never translated."""

from __future__ import annotations

import re
from typing import Literal, get_args

RenderLanguage = Literal["en", "de", "fr", "es"]

# Keys remain stable regardless of the display language.
_LABEL_ROWS = {
    "base": ("Base résumé", "Basislebenslauf", "CV de base", "Currículum base"),
    "resume": ("Résumé", "Lebenslauf", "CV", "Currículum"),
    "cover_letter": (
        "Cover letter",
        "Anschreiben",
        "Lettre de motivation",
        "Carta de presentación",
    ),
    "experience": (
        "Experience",
        "Berufserfahrung",
        "Expérience professionnelle",
        "Experiencia profesional",
    ),
    "education": ("Education", "Ausbildung", "Formation", "Formación"),
    "skill": ("Skills", "Kenntnisse", "Compétences", "Competencias"),
    "project": ("Projects", "Projekte", "Projets", "Proyectos"),
    "certification": ("Certifications", "Zertifizierungen", "Certifications", "Certificaciones"),
    "language": ("Languages", "Sprachen", "Langues", "Idiomas"),
    "other": ("Profile evidence", "Profilnachweise", "Éléments du profil", "Evidencias del perfil"),
    "publication": ("Publications", "Publikationen", "Publications", "Publicaciones"),
    "writing_fact": (
        "Additional facts",
        "Weitere Angaben",
        "Informations complémentaires",
        "Información adicional",
    ),
    "Application": ("Application", "Bewerbung", "Candidature", "Candidatura"),
    "Letter": ("Letter", "Anschreiben", "Lettre", "Carta"),
    "Interest": ("Interest", "Interesse", "Motivation", "Interés"),
    "Summary": ("Summary", "Kurzprofil", "Profil", "Perfil"),
    "age": ("Age", "Alter", "Âge", "Edad"),
    "references": ("References", "Referenzen", "Références", "Referencias"),
    "present": ("Present", "heute", "à ce jour", "actualidad"),
    "professional": (
        "Yours sincerely,",
        "Mit freundlichen Grüßen",
        "Veuillez agréer mes salutations distinguées.",
        "Atentamente,",
    ),
    "concise": ("Regards,", "Freundliche Grüße", "Cordialement,", "Saludos cordiales,"),
    "friendly": ("Best wishes,", "Herzliche Grüße", "Bien à vous,", "Un cordial saludo,"),
    "greeting": (
        "Dear Hiring Team,",
        "Sehr geehrtes Recruiting-Team,",
        "Madame, Monsieur,",
        "Estimado equipo de selección:",
    ),
    "recipient": ("Hiring team", "Recruiting-Team", "Équipe de recrutement", "Equipo de selección"),
}
LABELS: dict[RenderLanguage, dict[str, str]] = {
    language: {key: row[index] for key, row in _LABEL_ROWS.items()}
    for index, language in enumerate(get_args(RenderLanguage))
}
MONTHS = {
    "de": [
        "Januar",
        "Februar",
        "März",
        "April",
        "Mai",
        "Juni",
        "Juli",
        "August",
        "September",
        "Oktober",
        "November",
        "Dezember",
    ],
    "fr": [
        "janvier",
        "février",
        "mars",
        "avril",
        "mai",
        "juin",
        "juillet",
        "août",
        "septembre",
        "octobre",
        "novembre",
        "décembre",
    ],
    "es": [
        "enero",
        "febrero",
        "marzo",
        "abril",
        "mayo",
        "junio",
        "julio",
        "agosto",
        "septiembre",
        "octubre",
        "noviembre",
        "diciembre",
    ],
}
_PROFICIENCY = {
    "native": ("Muttersprache", "Langue maternelle", "Lengua materna"),
    "fluent": ("Fließend", "Courant", "Fluido"),
    "professional": (
        "Berufliche Kenntnisse",
        "Compétence professionnelle",
        "Competencia profesional",
    ),
    "intermediate": ("Mittelstufe", "Intermédiaire", "Intermedio"),
    "basic": ("Grundkenntnisse", "Notions", "Básico"),
    "beginner": ("Anfängerkenntnisse", "Débutant", "Principiante"),
}


def format_date(value: str, language: RenderLanguage) -> str:
    """Gregorian dates at their saved precision; leave unknown owner wording alone."""
    if language == "en" or re.fullmatch(r"\d{4}(?:-\d{2}(?:-\d{2})?)?", value) is None:
        return value
    pieces = value.split("-")
    if len(pieces) == 1:
        return value
    month = int(pieces[1])
    if not 1 <= month <= 12:
        return value
    name = MONTHS[language][month - 1]
    year = pieces[0]
    if len(pieces) == 2:
        return f"{name} de {year}" if language == "es" else f"{name} {year}"
    day = int(pieces[2])
    if language == "de":
        return f"{day}. {name} {year}"
    if language == "fr":
        return f"{'1er' if day == 1 else day} {name} {year}"
    return f"{day} de {name} de {year}"


def display_field(key: str, value: str, language: RenderLanguage) -> str:
    if language == "en":
        return value
    if key in {"start_date", "end_date"}:
        if value.casefold() in {"present", "ongoing", "current"}:
            return LABELS[language]["present"]
        return format_date(value, language)
    if key == "proficiency" and value.casefold() in _PROFICIENCY:
        return _PROFICIENCY[value.casefold()][("de", "fr", "es").index(language)]
    return value


# One reviewed row per ownership key, in German / French / Spanish. Wording must
# not promote contribution, assistance or delivery into leadership or ownership.
VERB_ROWS = {
    "led": ("Leitete", "A dirigé", "Lideró"),
    "spearheaded": ("Leitete", "A mené", "Encabezó"),
    "headed": ("Leitete", "A dirigé", "Dirigió"),
    "directed": ("Leitete", "A dirigé", "Dirigió"),
    "owned": ("Verantwortete", "A eu la responsabilité de", "Asumió la responsabilidad de"),
    "took_ownership_of": (
        "Übernahm die Verantwortung für",
        "A pris la responsabilité de",
        "Asumió la responsabilidad de",
    ),
    "responsible_for": ("Verantwortlich für", "Responsable de", "Responsable de"),
    "was_responsible_for": ("War verantwortlich für", "A été responsable de", "Fue responsable de"),
    "managed": ("Verwaltete", "A géré", "Gestionó"),
    "supervised": ("Beaufsichtigte", "A supervisé", "Supervisó"),
    "oversaw": ("Beaufsichtigte", "A supervisé", "Supervisó"),
    "initiated": ("Initiierte", "A initié", "Inició"),
    "founded": ("Gründete", "A fondé", "Fundó"),
    "established": ("Etablierte", "A établi", "Estableció"),
    "introduced": ("Einführung:", "A introduit", "Introdujo"),
    "pioneered": ("Leistete Pionierarbeit für", "A ouvert la voie à", "Realizó trabajo pionero en"),
    "designed": ("Entwarf", "A conçu", "Diseñó"),
    "architected": (
        "Entwarf die Architektur für",
        "A conçu l’architecture de",
        "Diseñó la arquitectura de",
    ),
    "planned": ("Plante", "A planifié", "Planificó"),
    "built": ("Baute", "A construit", "Construyó"),
    "developed": ("Entwickelte", "A développé", "Desarrolló"),
    "implemented": ("Implementierte", "A mis en œuvre", "Implementó"),
    "created": ("Erstellte", "A créé", "Creó"),
    "engineered": ("Entwickelte", "A développé", "Desarrolló"),
    "coded": ("Programmierte", "A codé", "Codificó"),
    "programmed": ("Programmierte", "A programmé", "Programó"),
    "automated": ("Automatisierte", "A automatisé", "Automatizó"),
    "integrated": ("Integrierte", "A intégré", "Integró"),
    "migrated": ("Migrierte", "A migré", "Migró"),
    "prototyped": (
        "Erstellte einen Prototyp für",
        "A réalisé un prototype de",
        "Creó un prototipo de",
    ),
    "delivered": ("Lieferte", "A livré", "Entregó"),
    "shipped": ("Lieferte", "A livré", "Entregó"),
    "launched": ("Startete", "A lancé", "Lanzó"),
    "completed": ("Vollendete", "A terminé", "Completó"),
    "executed": ("Realisierte", "A exécuté", "Ejecutó"),
    "released": ("Veröffentlichte", "A publié", "Publicó"),
    "rolled_out": ("Einführung:", "A déployé", "Implantó"),
    "deployed": ("Bereitstellung:", "A déployé", "Desplegó"),
    "increased": ("Erhöhte", "A augmenté", "Aumentó"),
    "grew": ("Steigerte", "A accru", "Incrementó"),
    "raised": ("Erhöhte", "A augmenté", "Elevó"),
    "boosted": ("Steigerte", "A accru", "Incrementó"),
    "expanded": ("Erweiterte", "A élargi", "Amplió"),
    "scaled": ("Skalierte", "A accru l’échelle de", "Escaló"),
    "generated": ("Erzeugte", "A généré", "Generó"),
    "achieved": ("Erreichte", "A atteint", "Logró"),
    "reduced": ("Reduzierte", "A réduit", "Redujo"),
    "cut": ("Senkte", "A réduit", "Recortó"),
    "decreased": ("Verringerte", "A diminué", "Disminuyó"),
    "lowered": ("Senkte", "A abaissé", "Redujo"),
    "shortened": ("Verkürzte", "A raccourci", "Acortó"),
    "minimized": ("Minimierte", "A minimisé", "Minimizó"),
    "slashed": ("Senkte deutlich", "A fortement réduit", "Redujo considerablemente"),
    "trimmed": ("Verringerte", "A réduit", "Redujo"),
    "saved": ("Sparte", "A économisé", "Ahorró"),
    "improved": ("Verbesserte", "A amélioré", "Mejoró"),
    "optimized": ("Optimierte", "A optimisé", "Optimizó"),
    "enhanced": ("Verbesserte", "A amélioré", "Mejoró"),
    "streamlined": ("Vereinfachte", "A rationalisé", "Simplificó"),
    "modernized": ("Modernisierte", "A modernisé", "Modernizó"),
    "stabilized": ("Stabilisierte", "A stabilisé", "Estabilizó"),
    "strengthened": ("Stärkte", "A renforcé", "Reforzó"),
    "hardened": ("Härtete", "A renforcé la sécurité de", "Reforzó la seguridad de"),
    "accelerated": ("Beschleunigte", "A accéléré", "Aceleró"),
    "resolved": ("Löste", "A résolu", "Resolvió"),
    "fixed": ("Behob", "A corrigé", "Corrigió"),
    "wrote": ("Schrieb", "A écrit", "Escribió"),
    "authored": ("Verfasste", "A rédigé", "Redactó"),
    "documented": ("Dokumentierte", "A documenté", "Documentó"),
    "drafted": ("Entwarf", "A rédigé une ébauche de", "Redactó un borrador de"),
    "produced": ("Produzierte", "A produit", "Produjo"),
    "prepared": ("Vorbereitung:", "A préparé", "Preparó"),
    "published": ("Veröffentlichte", "A publié", "Publicó"),
    "presented": ("Präsentierte", "A présenté", "Presentó"),
    "maintained": ("Wartete", "A maintenu", "Mantuvo"),
    "administered": ("Administrierte", "A administré", "Administró"),
    "operated": ("Betrieb", "A exploité", "Operó"),
    "configured": ("Konfigurierte", "A configuré", "Configuró"),
    "monitored": ("Überwachte", "A surveillé", "Supervisó"),
    "upgraded": ("Aktualisierte", "A mis à niveau", "Actualizó"),
    "refactored": ("Refaktorierte", "A remanié", "Refactorizó"),
    "mentored": ("Begleitete im Mentoring", "A accompagné comme mentor", "Ofreció mentoría a"),
    "coached": ("Coachte", "A accompagné", "Asesoró a"),
    "trained": ("Schulte", "A formé", "Formó a"),
    "analyzed": ("Analysierte", "A analysé", "Analizó"),
    "assessed": ("Bewertete", "A évalué", "Evaluó"),
    "audited": ("Prüfte", "A audité", "Auditó"),
    "evaluated": ("Evaluierte", "A évalué", "Evaluó"),
    "investigated": ("Untersuchte", "A examiné", "Investigó"),
    "researched": ("Erforschte", "A étudié", "Investigó"),
    "reviewed": ("Prüfte", "A examiné", "Revisó"),
    "measured": ("Maß", "A mesuré", "Midió"),
    "inspected": ("Inspizierte", "A inspecté", "Inspeccionó"),
    "conducted": ("Durchführung:", "A réalisé", "Realizó"),
    "identified": ("Identifizierte", "A identifié", "Identificó"),
    "defined": ("Definierte", "A défini", "Definió"),
    "coordinated": ("Koordinierte", "A coordonné", "Coordinó"),
    "orchestrated": ("Orchestrierte", "A orchestré", "Orquestó"),
    "organized": ("Organisierte", "A organisé", "Organizó"),
    "facilitated": ("Moderierte", "A facilité", "Facilitó"),
    "scheduled": ("Terminierte", "A programmé", "Programó"),
    "negotiated": ("Verhandelte", "A négocié", "Negoció"),
    "partnered": ("Kooperation:", "A coopéré à", "Cooperó en"),
    "collaborated": ("Zusammenarbeit:", "A collaboré à", "Colaboró en"),
    "contributed": ("Mitwirkung:", "A contribué à", "Contribuyó a"),
    "contributed_to": ("Mitwirkung:", "A contribué à", "Contribuyó a"),
    "worked_on": ("Arbeit:", "A travaillé sur", "Trabajó en"),
    "participated_in": ("Beteiligung:", "A participé à", "Participó en"),
    "took_part_in": ("Teilnahme:", "A pris part à", "Tomó parte en"),
    "supported": ("Unterstützte", "A soutenu", "Apoyó"),
    "handled": ("Bearbeitete", "A traité", "Gestionó"),
    "processed": ("Verarbeitete", "A traité", "Procesó"),
    "answered": ("Beantwortete", "A répondu à", "Contestó a"),
    "responded": ("Antwortete auf", "A répondu à", "Respondió a"),
    "assisted_with": ("Hilfestellung:", "A aidé à", "Ayudó con"),
    "assisted_in": ("Hilfestellung:", "A aidé à", "Ayudó en"),
    "helped": ("Hilfestellung:", "A aidé à", "Ayudó con"),
    "helped_to": ("Hilfestellung:", "A aidé à", "Ayudó a"),
    "assisted": ("Assistenz:", "A apporté une aide à", "Prestó ayuda en"),
}
