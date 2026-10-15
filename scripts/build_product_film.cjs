/** Assemble the canonical network UI and product story into exactly 60 seconds.
 * NODE_PATH=/path/to/node_modules node scripts/build_product_film.cjs
 */
const {spawn}=require('node:child_process'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const root=path.resolve(__dirname,'..');
async function run(cmd,args,env={}){await new Promise((resolve,reject)=>{const p=spawn(cmd,args,{cwd:root,env:{...process.env,...env},stdio:'inherit'});p.on('error',reject);p.on('close',code=>code?reject(Error(cmd+' exited '+code)):resolve())})}
(async()=>{
 const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'efferents-film-'));
 console.log('Rendering canonical UI opening');
 await run(process.execPath,['scripts/render_network_film.cjs'],{FILM_FINAL:'1',FILM_OUTPUT:path.join(tmp,'network.mp4')});
 console.log('Rendering ideas, evidence, steering, governance and closing');
 await run(process.execPath,['scripts/render_product_film.cjs'],{FILM_CONTINUATION:'1',FILM_SECONDS:'45.4',FILM_OUTPUT:path.join(tmp,'story.mp4'),FILM_POSTER:path.join(tmp,'story-poster.png')});
 const out=path.join(root,'docs/img/efferents-product-film.mp4');
 await run('ffmpeg',['-y','-loglevel','error','-i',path.join(tmp,'network.mp4'),'-i',path.join(tmp,'story.mp4'),'-filter_complex','[0:v]settb=AVTB,setpts=PTS-STARTPTS[a];[1:v]settb=AVTB,setpts=PTS-STARTPTS[b];[a][b]xfade=transition=fade:duration=0.4:offset=14.6,format=yuv420p[v]','-map','[v]','-an','-t','60','-r','30','-c:v','libx264','-crf','19','-preset','medium','-movflags','+faststart',path.join(tmp,'final.mp4')]);
 await run('ffmpeg',['-y','-loglevel','error','-ss','8','-i',path.join(tmp,'final.mp4'),'-frames:v','1',path.join(root,'docs/img/efferents-product-film-poster.png')]);
 fs.copyFileSync(path.join(tmp,'final.mp4'),out);
 await run('ffmpeg',['-v','error','-i',out,'-f','null','-']);
 console.log(out);
})().catch(e=>{console.error(e);process.exitCode=1});
