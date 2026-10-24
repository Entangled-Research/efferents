"""Shared journal routing vocabulary; routing is not publication or acceptance."""

import re

# Ordered rules: the first matching journal wins. Keep short aliases exact so
# that, for example, "html" does not match "ml".
JOURNAL_TAGS = {
    "Biology & Protein Science": ("biology", "biological", "protein", "gfp", "genetics", "bioinformatics"),
    "Chemistry": ("chemistry", "chemical", "reaction-mechanism", "cheminformatics"),
    "Journal of Numerical Analysis": (),
    "ML & Autonomous Systems": ("machine-learning", "active-learning", "artificial-intelligence", "computer-vision", "image-diffusion", "flow-matching", "generative-model"),
    "Simulation & Autonomous Systems": ("vehicle", "traffic", "simulation", "routing", "transport"),
    "Physics & Dynamics": ("physics", "orbit", "mechanics", "quantum", "fluid-dynamics", "aerodynamic", "airfoil"),
    "Computing & Security": ("program-security", "computer-security", "cybersecurity", "hardware-dependent", "software-security"),
    "Mathematics & Computation": ("math", "graph", "numerical", "algorithm", "optimisation", "optimization"),
}
JOURNAL_EXACT_TAGS = {
    "Journal of Numerical Analysis": ("numerical-analysis",),
    "ML & Autonomous Systems": ("ml",),
}
JOURNAL_FIELDS = {
    "Biology & Protein Science": "biology",
    "Computing & Security": "computing",
    "ML & Autonomous Systems": "computing",
    "Simulation & Autonomous Systems": "engineering",
    "Physics & Dynamics": "physics",
    "Mathematics & Computation": "mathematics",
    "Journal of Numerical Analysis": "mathematics",
}
FIELD_TAGS = {
    "biology": {"biology", "biological", "ecology", "genetics", "neuroscience", "bioinformatics", "protein", "gfp"},
    "chemistry": {"chemistry", "chemical", "materials"},
    "earth": {"earth", "climate", "geology", "geoscience", "environmental"},
    "computing": {"computing", "computer", "robotics"},
    "engineering": {"engineering", "electrical", "energy"},
}
RELATED_FIELDS = {
    frozenset(pair) for pair in (
        ("computing", "engineering"), ("computing", "biology"),
        ("computing", "physics"), ("physics", "engineering"),
        ("physics", "chemistry"), ("physics", "earth"),
        ("chemistry", "biology"), ("chemistry", "earth"),
        ("chemistry", "engineering"), ("biology", "earth"),
    )
}

# Shared by the daemon and the standalone event gateway. Unknown domains stay
# in their own journal until an explicit disciplinary relationship is defined.
INTERDISCIPLINARY_EVERY = 5


def journal_for_domain(domain: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", domain.casefold()).strip("-")
    for journal, tags in JOURNAL_TAGS.items():
        if value in JOURNAL_EXACT_TAGS.get(journal, ()) or any(tag in value for tag in tags):
            return journal
    return domain.replace("-", " ").strip().title() or "General Research"


def stem_field(domain: str) -> str | None:
    journal = journal_for_domain(domain)
    if journal in JOURNAL_FIELDS:
        return JOURNAL_FIELDS[journal]
    tokens = set(re.findall(r"[a-z0-9]+", domain.casefold()))
    for field, tags in FIELD_TAGS.items():
        if tokens & tags:
            return field
    return None


def related_stem_domains(source: str, target: str) -> bool:
    """Eligibility for an occasional read, never permission to submit elsewhere."""
    left, right = stem_field(source), stem_field(target)
    if not left or not right:
        return False
    if left == right or "mathematics" in {left, right}:
        return True
    return frozenset((left, right)) in RELATED_FIELDS
