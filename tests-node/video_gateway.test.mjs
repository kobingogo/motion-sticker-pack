import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtemp, mkdir, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import { ADAPTERS, checkProvider, execute } from '../scripts/video_gateway.mjs';

const sha = (value) => createHash('sha256').update(value).digest('hex');

test('all declared AI SDK adapters import and expose video factories', async () => {
  for (const [provider, adapter] of Object.entries(ADAPTERS)) {
    const status = await checkProvider(provider, adapter.package);
    assert.equal(status.available, true, `${provider}: ${JSON.stringify(status)}`);
  }
});

test('package substitution is rejected before dynamic import', async () => {
  assert.deepEqual(await checkProvider('xai', '@ai-sdk/fal'), {
    available: false,
    reason: 'package-provider-mismatch',
  });
});

test('unapproved revision fails before any provider request', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'motion-sticker-pack-gateway-'));
  const image = path.join(root, 'sheet.png');
  const layout = path.join(root, 'layout.json');
  const prompt = path.join(root, 'prompts.json');
  const approval = path.join(root, 'job-state.json');
  const config = path.join(root, 'providers.json');
  const task = path.join(root, 'task.json');
  const outputDirectory = path.join(root, 'raw');
  const result = path.join(root, 'result.json');
  const imageBytes = Buffer.from('not-a-real-image-but-no-provider-must-see-it');
  const layoutBytes = Buffer.from(JSON.stringify({ detected_layout: { columns: 1, rows: 1, count: 1, confidence: 0.95 } }));
  await writeFile(image, imageBytes);
  await writeFile(layout, layoutBytes);
  await writeFile(prompt, JSON.stringify({ detected_layout: { columns: 1, rows: 1, count: 1, confidence: 0.95 }, grid_video_prompt: 'move' }));
  await writeFile(approval, JSON.stringify({
    version: 1,
    phase: 'static-review',
    revision: sha(imageBytes),
    static_image: { sha256: sha(imageBytes) },
    layout: { sha256: sha(layoutBytes) },
    approval: null,
  }));
  await writeFile(config, JSON.stringify({
    version: 1,
    providers: [{
      id: 'xai', driver: 'ai-sdk', provider: 'xai', package: '@ai-sdk/xai',
      model: 'grok-imagine-video', enabled: true,
    }],
  }));
  await mkdir(outputDirectory);
  await writeFile(task, JSON.stringify({
    version: 1,
    operation: 'image-to-video',
    input_image: image,
    layout_file: layout,
    prompt_file: prompt,
    approval_file: approval,
    output_directory: outputDirectory,
  }));
  await assert.rejects(
    execute({ configFile: config, taskFile: task, providerId: 'xai', resultFile: result }),
    /not approved/,
  );
});
