"""Shared journal routing vocabulary; routing is not publication or acceptance."""

def journal_for_domain(domain: str) -> str:
    value = domain.casefold().replace("_", "-")
    if value == "numerical-analysis":
        return "Journal of Numerical Analysis"
    if value == "ml" or any(tag in value for tag in ("machine-learning", "active-learning", "artificial-intelligence")):
        return "ML & Autonomous Systems"
    if any(tag in value for tag in ("vehicle", "traffic", "simulation", "routing", "transport")):
        return "Simulation & Autonomous Systems"
    if any(tag in value for tag in ("physics", "orbit", "mechanics")):
        return "Physics & Dynamics"
    if any(tag in value for tag in ("math", "graph", "numerical", "algorithm", "optimisation", "optimization")):
        return "Mathematics & Computation"
    return domain.replace("-", " ").strip().title() or "General Research"


# Shared by the daemon and the standalone event gateway. Unknown domains stay
# in their own journal until an explicit disciplinary relationship is defined.
INTERDISCIPLINARY_EVERY = 5


def stem_field(domain: str) -> str | None:
    journal = journal_for_domain(domain)
    known = {
        "ML & Autonomous Systems": "computing",
        "Simulation & Autonomous Systems": "engineering",
        "Physics & Dynamics": "physics",
        "Mathematics & Computation": "mathematics",
        "Journal of Numerical Analysis": "mathematics",
    }
    if journal in known:
        return known[journal]
    tokens = set(domain.casefold().replace("_", "-").split("-"))
    for field, tags in {
        "biology": {"biology", "biological", "ecology", "genetics", "neuroscience", "bioinformatics"},
        "chemistry": {"chemistry", "chemical", "materials"},
        "earth": {"earth", "climate", "geology", "geoscience", "environmental"},
        "computing": {"computing", "computer", "robotics"},
        "engineering": {"engineering", "electrical", "energy"},
    }.items():
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
    neighbors = {
        frozenset(pair) for pair in (
            ("computing", "engineering"), ("computing", "biology"),
            ("computing", "physics"), ("physics", "engineering"),
            ("physics", "chemistry"), ("physics", "earth"),
            ("chemistry", "biology"), ("chemistry", "earth"),
            ("chemistry", "engineering"), ("biology", "earth"),
        )
    }
    return frozenset((left, right)) in neighbors
