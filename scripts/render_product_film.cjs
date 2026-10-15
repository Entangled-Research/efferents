/** Deterministic, offline film renderer. Requires playwright and ffmpeg.
 * NODE_PATH=/path/to/node_modules node scripts/render_product_film.cjs
 * Optional: FILM_FPS=30 FILM_SECONDS=65 FILM_OUTPUT=/absolute/output.mp4
 */
const {chromium}=require('playwright');
const {spawn}=require('node:child_process');
const fs=require('node:fs');
const path=require('node:path');
(async()=>{
 const root=path.resolve(__dirname,'..'),fps=Number(process.env.FILM_FPS||30),duration=Number(process.env.FILM_SECONDS||65);
 const out=process.env.FILM_OUTPUT||path.join(root,'docs/img/efferents-product-film.mp4');
 const browser=await chromium.launch({headless:true,channel:process.env.FILM_BROWSER||"chrome"});
 try {
 const page=await browser.newPage({viewport:{width:1600,height:900},deviceScaleFactor:1});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('file://'+(process.env.FILM_SOURCE||path.join(root,'docs/film/product-film.html'))+'?capture');
 if(process.env.FILM_CONTINUATION)await page.evaluate(()=>{
   const renderOriginal=window.renderFrame;
   window.renderFrame=t=>{
     const original=t<8.4?16.4+t/8.4*7.59:t<18.4?24+(t-8.4)*1.199:t<30.4?36+(t-18.4)*.999:t<40.4?48+(t-30.4)*1.199:60+(t-40.4)*.999;
     renderOriginal(original);
     c.fillStyle=C.bg;c.fillRect(40,884,1520,12);line(44,889,1556,889,C.line);line(44,889,44+1512*(t+14.6)/60,889,C.blue,3);
   };
 });
 for(const t of [0,8,12,18,25,29,39,45,55,62].filter(t=>t<duration)){
  await page.evaluate(t=>renderFrame(t),t);
  if(process.env.FILM_STILLS){fs.mkdirSync(process.env.FILM_STILLS,{recursive:true});await page.screenshot({path:path.join(process.env.FILM_STILLS,`${t}.png`)});}
 }
 await page.evaluate(()=>renderFrame(10));
 await page.screenshot({path:(process.env.FILM_POSTER||path.join(root,'docs/img/efferents-product-film-poster.png'))});
 if(process.env.FILM_STILLS_ONLY){if(errors.length)throw Error(errors.join('\n'));return;}
 const ff=spawn('ffmpeg',['-y','-loglevel','error','-f','image2pipe','-vcodec','mjpeg','-framerate',String(fps),'-i','pipe:0','-an','-c:v','libx264','-preset','medium','-crf','19','-pix_fmt','yuv420p','-movflags','+faststart',out],{stdio:['pipe','inherit','inherit']});
 const completion=new Promise((resolve,reject)=>{ff.on('error',reject);ff.on('close',code=>code===0?resolve():reject(Error('ffmpeg exit '+code)))});
 for(let n=0;n<duration*fps;n++){
  const data=await page.evaluate(t=>{renderFrame(t);return document.querySelector('canvas').toDataURL('image/jpeg',.94).split(',')[1]},n/fps);
  if(!ff.stdin.write(Buffer.from(data,'base64')))await new Promise(resolve=>ff.stdin.once('drain',resolve));
  if(n%(fps*5)===0)console.log(`Rendered ${n/fps}s / ${duration}s`);
 }
 ff.stdin.end();await completion;
 if(errors.length)throw Error(errors.join('\n'));
 console.log(out);
 } finally {await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
