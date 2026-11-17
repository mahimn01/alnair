import {chromium} from 'playwright';import fs from 'node:fs';import os from 'node:os';import path from 'node:path';
const output=process.env.ALNAIR_SCREEN_DIR||path.resolve('screens');fs.mkdirSync(output,{recursive:true});
const browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:1440,height:1000},deviceScaleFactor:2,reducedMotion:'reduce'});const errors=[];page.on('pageerror',e=>errors.push(String(e)));
try{
 await page.goto('http://127.0.0.1:1420/?demo=1');await page.getByTestId('component-overview').waitFor();await page.evaluate(()=>document.fonts.ready);await page.screenshot({path:path.join(output,'01-workspace.png')});
 await page.locator('.canvas-scroll').evaluate(el=>{el.style.overflow='visible'});await page.locator('.canvas-pane').evaluate(el=>{el.style.overflow='visible'});await page.locator('.workspace').evaluate(el=>{el.style.overflow='visible';el.style.flex='none';el.style.height='auto'});await page.locator('.app-shell').evaluate(el=>{el.style.height='auto';el.style.overflow='visible'});await page.locator('main').evaluate(el=>{el.style.overflow='visible'});await page.screenshot({path:path.join(output,'02-component-gallery.png'),fullPage:true});
 await page.reload();await page.getByTestId('component-overview').waitFor();await page.keyboard.press('Meta+k');await page.getByRole('dialog').waitFor();await page.screenshot({path:path.join(output,'03-command-palette.png')});await page.keyboard.press('Escape');
 await page.getByTestId('component-brief').scrollIntoViewIfNeeded();await page.getByLabel('Research focus').fill('Long-context research');await page.getByRole('button',{name:'Send answer',exact:true}).click();await page.getByRole('button',{name:'Answer sent',exact:true}).waitFor();await page.screenshot({path:path.join(output,'04-interactive-form.png')});
 await page.goto('http://127.0.0.1:1420/');await page.getByText('Ideas deserve a canvas.').waitFor();await page.screenshot({path:path.join(output,'05-ready-state.png')});
 if(errors.length)throw Error(errors.join('\n'));
 console.log('5 screenshots; gallery, palette, form answer and ready-state checks passed; no model calls');
}finally{await browser.close()}
