/** Capture the canonical dashboard UI, with authored fixtures and a 15-second camera. */
const {chromium}=require('playwright');
const fs=require('node:fs'),path=require('node:path'),{spawn}=require('node:child_process');
(async()=>{
 const root=path.resolve(__dirname,'..'),staticDir=path.join(root,'efferents/dashboard/static');
 const html=fs.readFileSync(path.join(staticDir,'dashboard.html'),'utf8').replace(/<script[\s\S]*?<\/script>/g,'').replace(/<link[^>]+>/g,'');
 const css=fs.readFileSync(path.join(staticDir,'dashboard.css'),'utf8');
 const js=fs.readFileSync(path.join(staticDir,'dashboard.js'),'utf8').split('\ninitRouting();')[0].replaceAll('performance.now()','0');
 const fixture=fs.readFileSync(path.join(root,'docs/film/network-study.js'),'utf8');
 const framing=`body{overflow:hidden}body>*:not(#workspace-frame):not(#film-header):not(#film-caption):not(#film-footer){display:none!important}#workspace-frame{display:block!important}main{padding:0!important;margin:0!important}main>*:not(#network-view){display:none!important}#network-view{display:block!important;position:absolute;inset:150px 30px 65px;margin:0!important}#network-view>*:not(.network-grid){display:none!important}.network-grid{display:block!important;margin:0!important}.network-grid>*:not(.network-map-panel){display:none!important}.network-map-panel{margin:0!important}.network-map-panel>.panel-header{height:58px}.lab-map{height:570px!important;min-height:0!important;max-height:570px!important}.map-controls,.map-pan-cue,.map-legend,.network-hub{display:none!important}#film-header{position:absolute;top:24px;left:34px;right:34px;color:var(--signal-strong)}#film-header strong{font:600 27px var(--mono)}#film-header h1{font:500 34px/1.2 var(--sans);margin:17px 0 0}#film-caption{position:absolute;bottom:82px;left:50px;padding:12px 18px;background:var(--panel);border:1px solid var(--signal);font:13px var(--mono);color:var(--signal-strong);z-index:100}#film-footer{position:absolute;bottom:22px;left:34px;right:34px;font:10px var(--mono);color:var(--muted);display:flex;justify-content:space-between}`;
 const setup=`<style>${css}\n${framing}</style>`;
 const footer='<div id="film-header"><strong>ℯ efferents</strong><h1>A living network of scientific ideas.</h1></div><div id="film-caption"></div><div id="film-footer"><span>15-SECOND NETWORK STUDY / ILLUSTRATIVE DATA</span><span>CANONICAL DASHBOARD UI · PUBLICATION / READING / REVISION</span></div>';
 let document=html.replace('</head>',setup+'</head>').replace('</body>',footer+`<script>${js}</script><script>${fixture}</script></body>`);
 if(process.env.FILM_FINAL)document=document.replace('15-SECOND NETWORK STUDY / ILLUSTRATIVE DATA','PRODUCT FILM / ILLUSTRATIVE DATA');
 const preview=process.env.FILM_PREVIEW||'/tmp/efferents-network-ui-preview.html';fs.writeFileSync(preview,document);
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 try{
 const page=await browser.newPage({viewport:{width:1600,height:900},deviceScaleFactor:1});const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('file://'+preview+'?capture');
 for(const t of [0,5,9,13]){await page.evaluate(t=>renderFrame(t),t);await page.screenshot({path:`/tmp/efferents-canonical-${t}.png`})}
 if(errors.length)throw Error(errors.join('\n'));
 if(process.env.FILM_STILLS_ONLY)return;
 const output=process.env.FILM_OUTPUT||path.join(root,'docs/img/efferents-network-study.mp4'),fps=30;
 const ff=spawn('ffmpeg',['-y','-loglevel','error','-f','image2pipe','-vcodec','mjpeg','-framerate',String(fps),'-i','pipe:0','-an','-c:v','libx264','-preset','medium','-crf','19','-pix_fmt','yuv420p','-movflags','+faststart',output],{stdio:['pipe','inherit','inherit']});
 const complete=new Promise((resolve,reject)=>{ff.on('error',reject);ff.on('close',code=>code?reject(Error('ffmpeg '+code)):resolve())});
 for(let n=0;n<15*fps;n++){await page.evaluate(t=>renderFrame(t),n/fps);const data=await page.screenshot({type:'jpeg',quality:94});if(!ff.stdin.write(data))await new Promise(resolve=>ff.stdin.once('drain',resolve));if(n%(fps*3)===0)console.log(`Rendered ${n/fps}s / 15s`)}
 ff.stdin.end();await complete;if(errors.length)throw Error(errors.join('\n'));console.log(output);
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
