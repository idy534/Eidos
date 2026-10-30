// Always provide a local language directory; never use a CDN or shared cache.
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { createWorker } = require('tesseract.js');

async function main() {
  const [input, language = 'eng'] = process.argv.slice(2);
  if (!input || !/^(eng|chi_sim)(\+(eng|chi_sim))?$/.test(language)) {
    throw new Error('Usage: ocr.cjs IMAGE [eng|chi_sim|eng+chi_sim] [OUTPUT.pdf]');
  }
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'eidos-ocr-'));
  fs.chmodSync(directory, 0o700);
  let worker;
  try {
  for (const code of language.split('+')) {
    const data = require(`@tesseract.js-data/${code}`);
    fs.copyFileSync(path.join(data.langPath, `${code}.traineddata.gz`), path.join(directory, `${code}.traineddata.gz`));
  }
    worker = await createWorker(language, 1, { langPath: directory, cacheMethod: 'none', gzip: true });
    const result = await worker.recognize(fs.readFileSync(input), {}, { text: true, pdf: true });
    process.stdout.write(result.data.text);
    if (process.argv[4]) fs.writeFileSync(process.argv[4], Buffer.from(result.data.pdf));
  } finally {
    if (worker) await worker.terminate();
    fs.rmSync(directory, { recursive: true, force: true });
  }
}
main().catch((error) => { console.error(error.message); process.exitCode = 1; });
