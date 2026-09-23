"""Behavioral regressions for the actual workspace navigation JavaScript."""
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.fixture
def run_js():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for workspace navigation tests")
    source = (Path(__file__).resolve().parents[1] / "efferents/dashboard/static/dashboard.js").read_text().split("\ninitRouting();")[0]
    harness = '''
const assert = require('node:assert/strict');
const storage = new Map();
const localStorage = {getItem: key => storage.get(key) ?? null, setItem: (key,value) => storage.set(key,value)};
const window = {location: {hash: '#network'}};
const history = {replaceState: (_,__,href) => {window.location.hash = href;}};
const strip = {innerHTML: '', hidden: false, dataset: {}, contains: () => false,
  querySelector: () => null, querySelectorAll: () => []};
const document = {getElementById: () => strip, activeElement: null, hidden: false};
'''

    def execute(body):
        result = subprocess.run([node, "-e", harness + source + '''
portfolioState = {labs: [{lab_id:'a',display_name:'Alpha',status:'stopped'}, {lab_id:'b',display_name:'Beta',status:'stopped'}],findings:[],edges:[],observations:[],eventNetwork:null};
portfolioHydrated = true;
controlState = {connected:true,hydrated:true,lab_id:'a'};
selectedLabId = 'a';
''' + body], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    return execute


def test_only_open_pages_are_tabs_and_refresh_preserves_state(run_js):
    run_js('''
renderLabTabs();
assert(!strip.innerHTML.includes('Alpha'));
rememberWorkspaceTab(labHref('a'));
rememberWorkspaceTab(labHref('a'));
renderLabTabs();
assert.deepEqual(workspaceTabs, ['#observe/a']);
assert(strip.innerHTML.includes('aria-label="Close Alpha"'));
assert(!strip.innerHTML.includes('Beta'));
closeWorkspaceTab('#observe/a');
renderLabTabs();
assert.deepEqual(workspaceTabs, []);
assert.equal(window.location.hash, '#network');
assert.equal(storage.get('efferents-workspace-tabs'), '[]');
''')


def test_close_active_selects_right_then_left_then_network(run_js):
    run_js('''
renderRoute = () => renderLabTabs();
workspaceTabs = ['#observe/a', '#observe/b'];
window.location.hash = '#observe/a';
closeWorkspaceTab('#observe/a');
assert.equal(window.location.hash, '#observe/b');
assert.deepEqual(workspaceTabs, ['#observe/b']);
closeWorkspaceTab('#observe/b');
assert.equal(window.location.hash, '#network');
assert.deepEqual(workspaceTabs, []);
workspaceTabs = ['#observe/a', '#observe/b'];
window.location.hash = '#observe/b';
closeWorkspaceTab('#observe/b');
assert.equal(window.location.hash, '#observe/a');
''')


def test_background_close_does_not_switch_or_reopen(run_js):
    run_js('''
workspaceTabs = ['#observe/a', '#observe/b'];
window.location.hash = '#observe/b';
closeWorkspaceTab('#observe/a');
renderLabTabs();
assert.equal(window.location.hash, '#observe/b');
assert.deepEqual(workspaceTabs, ['#observe/b']);
assert(!strip.innerHTML.includes('Close Alpha'));
''')


def test_lab_identity_survives_history_navigation(run_js):
    run_js('''
window.location.hash = '#observe/a';
assert.equal(currentRoute(), 'observe');
assert.equal(routeLabId(), 'a');
window.location.hash = '#observe/b';
assert.equal(routeLabId(), 'b');
window.location.hash = '#observe/a';
assert.equal(routeLabId(), 'a');
assert.equal(labHref('lab with spaces'), '#observe/lab%20with%20spaces');
''')


def test_slow_lab_response_cannot_take_over_a_journal_or_network(run_js):
    run_js('''
(async () => {
  let resolve;
  getJSON = () => new Promise(done => {resolve = done;});
  let painted = false;
  renderControl = () => {painted = true;};
  renderRoute = () => {};
  window.location.hash = '#observe/a';
  const pending = selectPortfolioLab('a', false);
  window.location.hash = '#network';
  resolve({lab_id:'a'});
  await pending;
  assert.equal(painted, false);
  assert.equal(window.location.hash, '#network');
})();
''')


def test_delayed_idea_response_cannot_overwrite_other_idea(run_js):
    run_js('''
(async () => {
  let respond;
  getJSON = () => new Promise(done => {respond = done;});
  let paints = 0;
  renderIdeaSuite = () => {paints++;};
  window.location.hash = '#observe/a/idea/primary';
  const pending = refreshObserver();
  window.location.hash = '#observe/a/idea/second';
  respond({student_id:'primary'});
  await pending;
  assert.equal(paints, 0);
  assert.equal(routeLabId(), 'a');
  assert.equal(routeIdeaId(), 'second');
})();
''')
