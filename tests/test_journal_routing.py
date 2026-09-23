import pytest
from efferents.journals import journal_for_domain, related_stem_domains


@pytest.mark.parametrize("left,right,expected", [
    ("math", "biology", True), ("physics", "chemistry", True),
    ("routing", "physics", True), ("numerical-analysis", "math", True),
    ("routing", "literature", False), ("physics", "unknown", False),
    ("biology", "routing", False), ("html", "physics", False),
])
def test_cross_conference_eligibility_is_symmetric(left, right, expected):
    assert related_stem_domains(left, right) is expected
    assert related_stem_domains(right, left) is expected


def test_home_journal_numerical_analysis_and_ml_tokens():
    assert journal_for_domain("numerical-analysis") == "Journal of Numerical Analysis"
    assert journal_for_domain("html") == "Html"
    assert journal_for_domain("ml") == "ML & Autonomous Systems"
