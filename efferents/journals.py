"""Shared journal routing vocabulary; routing is not publication or acceptance."""

def journal_for_domain(domain: str) -> str:
    value = domain.casefold().replace("_", "-")
    if any(tag in value for tag in ("machine-learning", "active-learning", "ml", "artificial-intelligence")):
        return "ML & Autonomous Systems"
    if any(tag in value for tag in ("vehicle", "traffic", "simulation", "routing", "transport")):
        return "Simulation & Autonomous Systems"
    if any(tag in value for tag in ("physics", "orbit", "mechanics")):
        return "Physics & Dynamics"
    if any(tag in value for tag in ("math", "graph", "numerical", "algorithm", "optimisation", "optimization")):
        return "Mathematics & Computation"
    return domain.replace("-", " ").strip().title() or "General Research"
