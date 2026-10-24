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


def test_network_flow_uses_complete_accepted_papers_and_recorded_receipts():
    result = run_js("""
const labs = [{lab_id:'source',journal:'Models'}, {lab_id:'home',journal:'Models'},
 {lab_id:'visitor',journal:'Methods'}];
const paper = {id:'valid',kind:'publication',publication_status:'accepted',lab_id:'source',
 journal:'Models',review_scores:{critical:6,neutral:7,optimistic:8}};
const findings = [paper,
 {...paper,id:'rejected',publication_status:'rejected'},
 {...paper,id:'unfinished',review_scores:{critical:6,neutral:7}},
 {...paper,id:'idea',kind:'hypothesis'},
 {...paper,id:'bad-score',review_scores:{critical:6,neutral:7,optimistic:11}}];
const receipts = [{finding_id:'valid',target:'home'}, {finding_id:'valid',target:'home'},
 {finding_id:'valid',target:'visitor'}, {finding_id:'rejected',target:'visitor'},
 {finding_id:'unfinished',target:'visitor'}, {finding_id:'missing',target:'visitor'},
 {finding_id:'valid',target:'unknown-lab'}];
console.log(JSON.stringify(networkFlowRecords(labs,findings,receipts)));
""")
    assert [paper["id"] for paper in result["publications"]] == ["valid"]
    assert result["reading"] == [
        {"journal": "Models", "target": "home", "occasional": False},
        {"journal": "Models", "target": "visitor", "occasional": True},
    ]


def test_network_flow_never_invents_reading_from_membership_or_publication():
    result = run_js("""
const paper = {id:'legacy',kind:'publication',publication_status:'accepted',lab_id:'source',
 journal:'Models',review_scores:{critical:6,neutral:7,enthusiast:8}};
console.log(JSON.stringify(networkFlowRecords([{lab_id:'source',journal:'Models'},
 {lab_id:'peer',journal:'Models'}],[paper],[])));
""")
    assert len(result["publications"]) == 1
    assert result["reading"] == []


def test_gravity_counts_distinct_accepted_reading_and_ignores_duplicates():
    result = run_js("""
const labs = [{lab_id:'a',journal:'Models'}, {lab_id:'b',journal:'Models'}, {lab_id:'c',journal:'Methods'}];
const base = {kind:'publication',publication_status:'accepted',lab_id:'a',journal:'Models',review_scores:{critical:6,neutral:7,optimistic:8}};
const papers = [{...base,id:'p1'}, {...base,id:'p2'}, {...base,id:'bad',publication_status:'rejected'}];
const receipts = [{finding_id:'p1',target:'b'}, {finding_id:'p1',target:'b'},
 {finding_id:'p2',target:'b'}, {finding_id:'bad',target:'c'}, {finding_id:'missing',target:'c'}];
const valid = networkFlowRecords(labs,papers,receipts).publications;
console.log(JSON.stringify(networkAffinity(labs,valid,receipts)));
""")
    assert result == [{"a": "a", "b": "b", "weight": 2}]


def test_attraction_is_slow_frequency_weighted_and_leaves_unlinked_labs_still():
    result = run_js("""
const bodies = [{id:'a',x:0,y:0,width:100,height:100,ideas:2},
 {id:'b',x:400,y:0,width:100,height:100,ideas:2},
 {id:'c',x:800,y:0,width:100,height:100,ideas:2}];
const once = advanceNetworkGravity(bodies,[{a:'a',b:'b',weight:1}],{},.1);
const often = advanceNetworkGravity(bodies,[{a:'a',b:'b',weight:8}],{},.1);
console.log(JSON.stringify({once,often}));
""")
    assert 0 < result["once"]["a"]["x"] < result["often"]["a"]["x"] <= 0.125
    assert result["often"]["b"]["x"] < 0
    assert result["often"]["c"] == {"x": 0, "y": 0}


def test_attraction_preserves_clearance_and_bounded_displacement_over_time():
    result = run_js("""
const bodies = [{id:'a',x:0,y:0,width:100,height:100,ideas:1},
 {id:'b',x:220,y:0,width:100,height:100,ideas:5}];
let state={};
for(let i=0;i<10000;i++)state=advanceNetworkGravity(bodies,[{a:'a',b:'b',weight:8}],state,.1);
console.log(JSON.stringify(state));
""")
    assert 220 + result["b"]["x"] - result["a"]["x"] >= 130
    assert all((offset["x"] ** 2 + offset["y"] ** 2) ** .5 <= 72.001 for offset in result.values())


def test_organic_packing_varies_footprints_without_overlapping_measured_labs():
    result = run_js("""
const labs=Array.from({length:7},(_,i)=>({lab_id:'lab-'+i,ideas:Array.from({length:i%5+1},()=>({}))}));
const sizes=new Map(labs.map((lab,i)=>[lab.lab_id,{width:i%2?448:304,height:220+i*35}]));
const layout=organicGroupLayout(labs,sizes,false);
let overlap=false;
for(let i=0;i<layout.placements.length;i++)for(let j=i+1;j<layout.placements.length;j++){
 const a=layout.placements[i],b=layout.placements[j];
 if(Math.abs(a.x-b.x)<(a.size.width+b.size.width)/2&&a.y<b.y+b.size.height&&b.y<a.y+a.size.height)overlap=true;
}
console.log(JSON.stringify({overlap,cols:layout.cols,rows:new Set(layout.placements.map(p=>p.y)).size}));
""")
    assert not result["overlap"]
    assert 1 < result["cols"] <= 3
    assert result["rows"] > 3


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
const labs = Array.from({length:30},(_,i)=>({lab_id:'lab-'+i,ideas:Array.from({length:i%5+1},()=>({}))}));
const sizes = new Map(labs.map((lab,i)=>[lab.lab_id,{width:i%2?448:304,height:220+i%5*35}]));
const layout = organicGroupLayout(labs,sizes,false);
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


@pytest.mark.parametrize("status", ["running", "paused", "stopped"])
@pytest.mark.parametrize("owner_paused", [False, True])
@pytest.mark.parametrize("can_control,paused_demo", [(True, False), (False, False), (True, True)])
def test_playback_controls_remain_accessible_beside_delete_for_authorized_labs(
    status, owner_paused, can_control, paused_demo,
):
    result = run_js(f"""
renderPlaybackControls({json.dumps({'status': status, 'owner_paused': owner_paused, 'paused_demo': paused_demo})}, {json.dumps(can_control)});
console.log(JSON.stringify({{
  pause: !document.getElementById('pause-lab').hidden,
  resume: !document.getElementById('resume-lab').hidden,
}}));
""")
    allowed = can_control and not paused_demo
    assert result == {"pause": allowed and not owner_paused, "resume": allowed and owner_paused}
