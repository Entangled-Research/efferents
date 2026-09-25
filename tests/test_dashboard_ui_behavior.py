"""Exercise the actual local console helpers without a browser or server."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

JS = (Path(__file__).resolve().parents[1] / "efferents/dashboard/static/dashboard.js").read_text()
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node is needed for dashboard JavaScript checks")


def run_js(assertions):
    stub = """
const elements = new Map();
globalThis.window = {location:{hash:'#network'}};
globalThis.localStorage = {getItem:()=>null,setItem:()=>{}};
globalThis.document = {
  getElementById(id) {
    if (!elements.has(id)) elements.set(id, {textContent:'',innerHTML:'',style:{},hidden:false});
    return elements.get(id);
  },
  querySelectorAll:()=>[]
};
"""
    # Skip only the entrypoint; the real functions and state are evaluated intact.
    source = JS.rsplit("\ninitRouting();", 1)[0]
    result = subprocess.run([NODE, "-"], input=stub + source + assertions,
                            text=True, capture_output=True, check=True)
    return json.loads(result.stdout)


def test_each_idea_links_to_its_own_evaluation_and_escapes_content():
    result = run_js("""
const lab = {lab_id:'chem',display_name:'Chemistry',ideas:[
 {id:'mechanism',name:'Rare mechanisms',verdict:'undecided'},
 {id:'<other>',name:'<script>alert(1)</script>',verdict:'falsified'}
]};
portfolioState.labs = [lab];
console.log(JSON.stringify(ideaBranchMarkup(lab)));
""")
    assert 'href="#observe/chem/idea/mechanism"' in result
    assert 'href="#observe/chem/idea/%3Cother%3E"' in result
    assert "Ideas · 2" in result
    assert "<script>" not in result
    assert "&lt;script&gt;" in result
    assert 'lab-idea-branch falsified' in result


def test_receipt_alone_never_claims_experimental_use():
    result = run_js("""
const publication = {id:'paper1',kind:'publication',publication_status:'accepted',lab_id:'physics',journal:'Physics'};
portfolioState.labs = [{lab_id:'physics',journal:'Physics'}, {lab_id:'chem',journal:'Chemistry'}];
portfolioState.findings = [publication];
portfolioState.observations = [{finding_id:'paper1',target:'chem',source:'physics'}];
const receiptOnly = publicationUseMarkup(publication);
portfolioState.journal_uses = [{finding_id:'paper1',source:'physics',target:'chem',
 local_campaign_id:'rare-mechanisms',run_ids:['chem-run-1'],use_kind:'method_or_design',
 reproduction_status:'not_verified',why:'Compare a calibration method'}];
const used = publicationUseMarkup(publication);
portfolioState.journal_uses.push({finding_id:'unaccepted',source:'physics',target:'chem'});
console.log(JSON.stringify({receiptOnly,used,count:journalUses().length}));
""")
    assert result["receiptOnly"] == ""
    assert "cross-domain" in result["used"]
    assert "reproduction not verified" in result["used"]
    assert "chem-run-1" in result["used"]
    assert "rare-mechanisms" in result["used"]
    assert result["count"] == 1


def test_null_metrics_remain_missing_and_thirty_labs_wrap():
    result = run_js("""
document.getElementById("lab-map").clientWidth = 1200;
document.getElementById("lab-map").clientHeight = 750;
const layout = chooseMapLayout([30]);
console.log(JSON.stringify({missing:[null,undefined,'','unknown'].map(hasMetricValue),
 zero:hasMetricValue(0),layout}));
""")
    assert not any(result["missing"])
    assert result["zero"]
    assert 1 < result["layout"]["cols"] < 30


def test_local_lab_spend_is_a_number_and_preserves_configured_budget():
    result = run_js("""
portfolioState.labs = [{lab_id:'one',budget:{spent:3,cap:5}}];
portfolioBudget = {spent:3,cap:5};
renderBudget();
console.log(JSON.stringify({summary:document.getElementById('budget').textContent,
 lab:labSpendMarkup(portfolioState.labs[0])}));
""")
    assert "$3.00 / $5.00 daily" in result["summary"]
    assert "$3.00" in result["lab"] and "$5.00" not in result["lab"]
