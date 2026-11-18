import fs from 'node:fs/promises';
import { SpreadsheetFile, Workbook } from '@oai/artifact-tool';

const root = process.cwd();
const outputDir = `${root}/outputs/shade-review`;
const rows = JSON.parse(await fs.readFile(`${outputDir}/published_shades.json`, 'utf8'));
const importReport = JSON.parse(await fs.readFile(`${root}/data/user-attribute-import-report.json`, 'utf8'));
const workbook = Workbook.create();

function makeSheet(name, title, note, headers, data, widths) {
  const sheet = workbook.worksheets.add(name);
  sheet.showGridLines = false;
  const matrix = [[title], ['Note', note], headers, ...data];
  const lastCol = String.fromCharCode(64 + headers.length);
  sheet.getRange(`A1:${lastCol}${matrix.length}`).values = matrix;
  sheet.getRange(`A1:${lastCol}1`).format = { fill: '#3F3A35', font: { name: 'Aptos Display', size: 16, bold: true, color: '#FFFFFF' } };
  sheet.getRange(`A2:${lastCol}2`).format = { fill: '#F4F0EB', font: { name: 'Aptos', size: 10, color: '#554F49' }, wrapText: true };
  sheet.getRange(`A3:${lastCol}3`).format = { fill: '#8B6856', font: { name: 'Aptos', size: 10, bold: true, color: '#FFFFFF' }, wrapText: true };
  sheet.getRange(`A4:${lastCol}${matrix.length}`).format = { font: { name: 'Aptos', size: 10, color: '#332F2A' }, wrapText: true, verticalAlignment: 'center' };
  sheet.getRange(`A1:${lastCol}1`).format.rowHeight = 30;
  sheet.getRange(`A2:${lastCol}2`).format.rowHeight = 34;
  sheet.getRange(`A3:${lastCol}3`).format.rowHeight = 32;
  sheet.getRange(`A4:${lastCol}${matrix.length}`).format.rowHeight = 30;
  widths.forEach((width, i) => { sheet.getRange(`${String.fromCharCode(65+i)}:${String.fromCharCode(65+i)}`).format.columnWidth = width; });
  sheet.freezePanes.freezeRows(3);
  return sheet;
}

const shadeHeaders = ['Brand', 'Product', 'Version', 'Product type', 'Shade', 'Code', 'Depth', 'Undertone', 'Original description', 'Depth evidence references', 'Undertone evidence references', 'Normalization state', 'Depth confidence', 'Undertone confidence', 'Availability', 'Publication state', 'Published match count', 'Published match details', 'Identity evidence references', 'Shade ID'];
const shadeData = rows.map(r => [r.brand, r.product, r.version || '', r.format || '', r.shade, r.code || '', r.depth || 'Not available', r.undertone || 'Not available', r.original_description || '', r.depth_evidence_refs || '', r.undertone_evidence_refs || '', r.normalization_state, r.depth_confidence, r.undertone_confidence, r.availability, r.publication_state, r.published_match_count, r.published_match_details || '', r.evidence_refs || '', r.shade_id]);
makeSheet('All shade records', 'Teinté | All shade records', 'Snapshot of every shade row in the app database, including archived rows. “Published” is app state, not independent verification. Blank or “Not available” means no normalized value is stored.', shadeHeaders, shadeData, [22, 34, 24, 22, 24, 16, 18, 18, 42, 48, 48, 22, 18, 20, 18, 20, 18, 62, 58, 38]);

const claimHeaders = ['From brand', 'From product', 'From shade', 'Source wording', 'Closeness label', 'Evidence confidence', 'To brand', 'To product', 'To shade', 'Caveat', 'Source file', 'Source page', 'Evidence references', 'From shade ID'];
const claimData = [];
for (const r of rows) for (const m of r.matches) claimData.push([r.brand, r.product, r.shade, m.source_wording, m.closeness || '', m.confidence || '', m.target_brand || 'Unresolved target', m.target_product || '', m.target_shade || '', m.caveat || '', m.source_file || '', m.source_page || '', Array.isArray(m.evidence_json) ? m.evidence_json.join('; ') : m.evidence_json || '', r.shade_id]);
makeSheet('Published match claims', 'Teinté | Published directed match claims', 'Each row preserves a one-way claim from the source shade to its named target. Unresolved targets are not presented as usable matches.', claimHeaders, claimData, [22, 34, 24, 36, 24, 20, 22, 34, 24, 54, 32, 14, 40, 38]);

const products = new Map();
for (const r of rows) {
  const key = `${r.brand}\u0000${r.product}\u0000${r.version || ''}\u0000${r.format || ''}`;
  const p = products.get(key) || { brand: r.brand, product: r.product, version: r.version || '', format: r.format || '', all: 0, published: 0, archived: 0, withMatches: 0, depth: 0, undertone: 0 };
  p.all++;
  if (r.publication_state === 'Published') { p.published++; if (r.published_match_count) p.withMatches++; if (r.depth) p.depth++; if (r.undertone) p.undertone++; }
  if (r.publication_state === 'Archived') p.archived++;
  products.set(key, p);
}
const productHeaders = ['Brand', 'Product', 'Version', 'Product type', 'All shade records', 'Published shade records', 'Archived shade records', 'Published shades with outgoing matches', 'Published depth present', 'Published undertone present'];
const productData = [...products.values()].sort((a,b)=>a.brand.localeCompare(b.brand)||a.product.localeCompare(b.product)).map(p=>[p.brand,p.product,p.version,p.format,p.all,p.published,p.archived,p.withMatches,p.depth,p.undertone]);
makeSheet('Product coverage', 'Teinté | Product shade coverage', 'Counts describe the current app database, separating published rows from archived rows. They are not proof that a product’s real-world shade range is complete.', productHeaders, productData, [24, 38, 26, 22, 18, 20, 20, 32, 24, 26]);

const notes = workbook.worksheets.add('Read me');
notes.showGridLines = false;
const publishedCount = rows.filter(r => r.publication_state === 'Published').length;
const archivedCount = rows.filter(r => r.publication_state === 'Archived').length;
notes.getRange('A1:B11').values = [
  ['Teinté shade database review', ''],
  ['What this is', 'A spreadsheet snapshot of all shade rows in data/teinte.sqlite3, plus published match claims.'],
  ['What it is not', 'It is not the complete unreviewed source catalogue and does not certify independent verification or real-world range completeness.'],
  ['Total shade rows in database', rows.length],
  ['Published shade rows', publishedCount],
  ['Archived shade rows', archivedCount],
  ['Published directed claims', claimData.length],
  ['Pasted shade entries included', importReport.website_included_entries],
  ['New product records', importReport.created_products.length],
  ['Product label note', importReport.product_mapping_note],
  ['Database file', `${root}/data/teinte.sqlite3`]
];
notes.getRange('A1:B1').format = { fill: '#3F3A35', font: { name: 'Aptos Display', size: 16, bold: true, color: '#FFFFFF' } };
notes.getRange('A2:A11').format = { fill: '#F4F0EB', font: { name: 'Aptos', size: 10, bold: true, color: '#554F49' }, wrapText: true };
notes.getRange('B2:B11').format = { font: { name: 'Aptos', size: 10, color: '#332F2A' }, wrapText: true };
notes.getRange('A:A').format.columnWidth = 28;
notes.getRange('B:B').format.columnWidth = 110;
notes.getRange('A1:B11').format.rowHeight = 34;

await workbook.recalculate();
const xlsx = await SpreadsheetFile.exportXlsx(workbook);
await xlsx.save(`${outputDir}/Teinte-shade-database-review.xlsx`);
const preview = await workbook.render({ sheetName: 'Product coverage', autoCrop: 'all', scale: 1, format: 'png' });
await fs.writeFile(`${outputDir}/product-coverage-preview.png`, new Uint8Array(await preview.arrayBuffer()));
const inspected = await workbook.inspect({ kind: 'sheet', include: 'id,name', maxChars: 2000 });
console.log(inspected.ndjson || inspected);
console.log(`Workbook created with ${rows.length} shade rows and ${claimData.length} claim rows.`);
