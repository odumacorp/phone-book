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
fs.writeFileSync(file, html);
console.log('web-postbuild: icon links added');
