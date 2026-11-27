// After `expo export --platform web`: add the Oduma Corp icon links to the page (browser tab + iPhone home screen)
// and copy the PNG favicon next to it.
const fs = require('fs');
const path = require('path');

const out = path.join(__dirname, '..', '..', 'web');
fs.copyFileSync(path.join(__dirname, '..', 'assets', 'favicon.png'), path.join(out, 'favicon.png'));

const file = path.join(out, 'index.html');
const links = [
  '<link rel="icon" type="image/png" sizes="256x256" href="/favicon.png" />',
  '<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png" />',
].join('');
let html = fs.readFileSync(file, 'utf8');
if (!html.includes('apple-touch-icon')) html = html.replace('</head>', `${links}</head>`);
// DM Sans (served by the website from /site/fonts), so the web app matches phone-book.odumacorp.com
const font = [
  '<link rel="preload" href="/site/fonts/dm-sans-latin.woff2" as="font" type="font/woff2" crossorigin />',
  '<style id="brand-font">@font-face{font-family:"DM Sans";src:url(/site/fonts/dm-sans-latin.woff2) format("woff2");font-weight:400 700;font-display:swap}',
  'body,#root div,#root input,#root textarea{font-family:"DM Sans",system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}',
  '#root div[data-mono]{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}</style>',
].join('');
if (!html.includes('brand-font')) html = html.replace('</head>', `${font}</head>`);
html = html.replace('content="#0B6E4F"', 'content="#1F6FEB"');
fs.writeFileSync(file, html);
console.log('web-postbuild: icon links and brand font added');
