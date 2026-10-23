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


@pytest.mark.parametrize('source,target',[
    ('synthetic image diffusion and spatial reasoning','GFP fluorescence prediction'),
    ('quantum data encoding and tensor networks','synthetic image diffusion and spatial reasoning'),
    ('computer vision','protein fitness landscapes'),
])
def test_natural_language_scientific_domains_reach_related_field_visits(source,target):
    assert related_stem_domains(source,target)
    assert related_stem_domains(target,source)


def test_diffusion_alias_does_not_route_physical_diffusion_to_ml():
    assert journal_for_domain('synthetic image diffusion and spatial reasoning') == 'ML & Autonomous Systems'
    assert journal_for_domain('machine learning') == 'ML & Autonomous Systems'
    assert journal_for_domain('molecular diffusion') != 'ML & Autonomous Systems'
    assert journal_for_domain('quantum data encoding and tensor networks') == 'Physics & Dynamics'
