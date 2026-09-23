from pathlib import Path
import re
from playwright.sync_api import sync_playwright
root=Path.cwd()
static=root/'efferents/dashboard/static'
html=re.sub(r'<script[\s\S]*?</script>', '', (static/'dashboard.html').read_text())
html=re.sub(r'<link[^>]+>', '', html)
js=(static/'dashboard.js').read_text().split('\ninitRouting();')[0]
with sync_playwright() as p:
    browser=p.chromium.launch()
    page=browser.new_page(viewport={'width':1440,'height':1000})
    errors=[]
    page.on('pageerror',lambda e: errors.append(str(e)))
    page.set_content(html)
    page.add_style_tag(content=(static/'dashboard.css').read_text())
    page.add_script_tag(content=js)
    page.evaluate('''() => {
      document.querySelectorAll('[data-route-view]').forEach(el => el.hidden = el.id !== 'network-view');
      const lab=(id,domain) => ({lab_id:id,domain,status:'stopped',budget:{spent:1,cap:10},headline:{column:'error',direction:'min',latest:0.02,best:0.01,observations:12},ideas:[{id:'primary',name:'Adaptive steps',focus:'Test adaptive integration',verdict:'undecided'},{id:'bounds',name:'Error bounds',focus:'Measure convergence',verdict:'falsified'}]});
      portfolioState={labs:[lab('math-one','math'),lab('math-two','math'),lab('physics-one','physics'),lab('physics-two','physics')],findings:[{id:'paper-1',lab_id:'math-one',domain:'math',journal:'Mathematics & Computation',kind:'publication',publication_status:'accepted',campaign_id:'campaign-1',body:'Accepted evidence',review_scores:{critical:6,neutral:7,optimistic:8}}],observations:[{finding_id:'paper-1',target:'physics-one'}],eventNetwork:null};
      renderNetwork(); initMapPanZoom();
    }''')
    page.screenshot(path='/tmp/conference-hierarchy.png',full_page=True)
    assert page.locator('.shared-journal-node').count()==2
    assert page.locator('.journal-route.visit').count()==1
    assert page.locator('.network-packet.accepted').count()==1
    assert page.locator('.network-lab-boundary').first.bounding_box()['y']>page.locator('.shared-journal-node').first.bounding_box()['y']
    page.locator('[data-lab-ideas]').first.click()
    assert page.get_by_role('dialog').is_visible()
    assert page.get_by_role('dialog').get_by_text('Test adaptive integration').is_visible()
    page.get_by_role('dialog').get_by_role('button',name='Evals',exact=True).click()
    assert page.get_by_role('dialog').get_by_text('0.02',exact=True).is_visible()
    page.get_by_role('button',name='Close',exact=True).click()
    page.locator('.shared-journal-node').first.click()
    assert page.get_by_role('dialog').get_by_text('campaign-1',exact=True).is_visible()
    page.get_by_text('Read publication',exact=True).click()
    assert page.get_by_role('dialog').get_by_text('Accepted evidence',exact=True).is_visible()
    page.keyboard.press('Escape')
    page.set_viewport_size({'width':390,'height':844})
    page.evaluate('renderNetwork()')
    page.locator('[data-map-zoom="fit"]').click()
    page.screenshot(path='/tmp/conference-hierarchy-mobile.png',full_page=True)
    assert not errors,errors
    print('Browser checks passed: hierarchy, ideas, eval summaries, journal reading, persisted animations, mobile fit')
    browser.close()
