import { createConverter } from '@deepseek-ai/libreoffice-kit';
import path from 'node:path';

const [operation, input, output, seconds = '120'] = process.argv.slice(2);
if (!['convert', 'recalculate'].includes(operation) || !input || !output || !process.env.EIDOS_DOCUMENT_FONTS) {
  throw new Error('Usage: bridge.mjs convert|recalculate INPUT OUTPUT [TIMEOUT_SECONDS]');
}
const converter = await createConverter({
  timeoutMs: Math.max(1000, Number(seconds) * 1000),
  fontDirectories: [process.env.EIDOS_DOCUMENT_FONTS],
  fontMetadataCacheDirectory: false,
});
try {
  const result = await converter[operation]({ inputPath: path.resolve(input), outputPath: path.resolve(output) });
  process.stdout.write(JSON.stringify(result));
} finally {
  await converter.dispose();
}
