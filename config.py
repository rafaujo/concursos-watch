"""Central configuration for Concursos Watch.

Keep the academic profile and all tunable policy here.  The classifier imports
this module; it does not duplicate profile facts in its implementation.
"""

from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"
DOCS_DIR = ROOT_DIR / "docs"

PCI_LISTING_URL = "https://www.pciconcursos.com.br/professores/"
SOURCE_NAME = "PCI Concursos"
USER_AGENT = "ConcursosWatch/1.0 (+https://github.com/; academic-vacancy-monitor)"
REQUEST_TIMEOUT_SECONDS = 30
REQUEST_DELAY_SECONDS = 1.25
REQUEST_RETRIES = 2
RECHECK_OPEN_AFTER_DAYS = 14
RECHECK_CLOSING_WITHIN_DAYS = 10

# Official-document stage. The crawler follows only a small, scored set of
# links found on institution/organizer pages and never bypasses CAPTCHA.
OFFICIAL_CHECK_ENABLED = True
OFFICIAL_READER_VERSION = 4
# None means every due professor notice is reviewed. The cache still prevents
# unchanged editais from being downloaded on every daily execution.
OFFICIAL_MAX_VACANCIES_PER_RUN = None
OFFICIAL_MAX_LINKS_PER_VACANCY = 8
OFFICIAL_MAX_DEPTH = 2
OFFICIAL_MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
OFFICIAL_MAX_PDF_PAGES = 400
OFFICIAL_MAX_EXTRACTED_CHARS = 2_000_000
OFFICIAL_RECHECK_AFTER_DAYS = 14
OFFICIAL_RETRY_AFTER_DAYS = 2
OFFICIAL_REQUEST_DELAY_SECONDS = 0.75

CLOSING_SOON_DAYS = 7
CLOSED_VISIBLE_DAYS = 30
STRONG_YES_SCORE = 60
STRONG_UNCERTAIN_SCORE = 75

PROFILE = {
    "undergraduate_degrees": [
        "Administração",
        "Administração Internacional de Negócios",
    ],
    "masters": {
        "title_area": "Ciências Ambientais e Conservação",
        "program": "Programa de Pós-Graduação em Ciências Ambientais e Conservação — PPG-CiAC",
        "institution": "UFRJ — Campus Macaé / NUPEM",
        "capes_evaluation_area": "Ciências Ambientais",
        "capes_broad_area": "Multidisciplinar",
        "academic_characteristic": "Interdisciplinar",
    },
    "doctorate": {
        "title_area": "Ciências Ambientais e Conservação",
        "program": "Programa de Pós-Graduação em Ciências Ambientais e Conservação — PPG-CiAC",
        "institution": "UFRJ — Campus Macaé / NUPEM",
        "capes_code": "31001017145D7",
        "capes_evaluation_area": "Ciências Ambientais",
        "capes_broad_area": "Multidisciplinar",
        "academic_characteristic": "Interdisciplinar",
    },
}

# Scores intentionally stack.  This makes an advert mentioning, for example,
# both socio-environmental management and sustainable development rank higher
# than a generic management advert.  The result is capped at 100.
THEMATIC_WEIGHTS = {
    "gestao socioambiental": 35,
    "ciencias ambientais": 30,
    "ciencias ambientais e conservacao": 10,
    "desenvolvimento sustentavel": 30,
    "gestao ambiental": 28,
    "sustentabilidade": 25,
    "administracao publica": 24,
    "gestao de politicas publicas": 24,
    "politicas publicas": 22,
    "administracao": 22,
    "gestao publica": 22,
    "responsabilidade social": 20,
    "economia ambiental": 20,
    "desenvolvimento territorial": 18,
    "planejamento territorial": 18,
    "desenvolvimento regional": 17,
    "desenvolvimento local": 16,
    "governanca": 15,
    "empreendedorismo": 15,
    "inovacao": 15,
    "gestao de projetos": 15,
    "estrategia": 14,
    "planejamento": 13,
    "desenvolvimento": 10,
    "gestao": 12,
    "multidisciplinar": 10,
    "interdisciplinar": 10,
    "esg": 12,
}

HIGHER_EDUCATION_HINTS = (
    "universidade", "instituto federal", "ifba", "if baiano", "ifpr",
    "cefet", "faculdade", "campus", "magisterio superior", "professor titular",
    "professor adjunto", "professor assistente", "professor doutor",
)

CHEAP_REJECT_HINTS = (
    "educacao infantil", "ensino infantil", "ensino fundamental",
    "professor de educacao basica", "peb i", "peb ii", "creche",
)

OFFICIAL_LINK_HINTS = (
    "edital", "concurso", "processo seletivo", "selecao", "professor",
    "docente", "magisterio", "retificacao", "prorrogacao", "resultado",
)

GEOGRAPHIC_PRIORITIES = {
    "BA": 1,
    "PR": 2,
    "SC": 3,
    "RS": 3,
}
