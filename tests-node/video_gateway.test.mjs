import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtemp, mkdir, readFile, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import {
  ADAPTERS,
  checkProvider,
  execute,
  providerFactorySettings,
  validateImageToVideoModel,
  validateProviderTaskSettings,
} from '../scripts/video_gateway.mjs';

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

test('provider factories map supported credential aliases and official regions', () => {
  const previous = {
    DASHSCOPE_API_KEY: process.env.DASHSCOPE_API_KEY,
    ARK_API_KEY: process.env.ARK_API_KEY,
  };
  process.env.DASHSCOPE_API_KEY = 'dashscope-test-key';
  process.env.ARK_API_KEY = 'ark-test-key';
  try {
    assert.deepEqual(providerFactorySettings({
      provider: 'alibaba', region: 'china', credentials: { env: ['DASHSCOPE_API_KEY'] },
    }), {
      apiKey: 'dashscope-test-key',
      baseURL: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
      videoBaseURL: 'https://dashscope.aliyuncs.com',
    });
    assert.deepEqual(providerFactorySettings({
      provider: 'bytedance', region: 'china', credentials: { env: ['ARK_API_KEY'] },
    }), {
      apiKey: 'ark-test-key',
      baseURL: 'https://ark.cn-beijing.volces.com/api/v3',
    });
  } finally {
    for (const [name, value] of Object.entries(previous)) {
      if (value === undefined) delete process.env[name];
      else process.env[name] = value;
    }
  }
});

test('direct gateway use rejects undeclared credential aliases', () => {
  process.env.UNRELATED_API_KEY = 'must-not-be-forwarded';
  try {
    assert.throws(() => providerFactorySettings({
      provider: 'alibaba', region: 'china', credentials: { env: ['UNRELATED_API_KEY'] },
    }), /unsupported credential environment set/);
  } finally {
    delete process.env.UNRELATED_API_KEY;
  }
});

test('direct gateway use rejects unknown provider options', async () => {
  const previous = process.env.ARK_API_KEY;
  process.env.ARK_API_KEY = 'test-key';
  try {
    const fixture = await approvedFixture({
      id: 'seedance', driver: 'ai-sdk', provider: 'bytedance', package: '@ai-sdk/bytedance',
      model: 'seedance-1-5-pro-251215', enabled: true,
      credentials: { env: ['ARK_API_KEY'] }, provider_options: { cameraFiexed: true },
    });
    await assert.rejects(
      execute({ configFile: fixture.config, taskFile: fixture.task, providerId: 'seedance', resultFile: fixture.result }),
      /unsupported provider_options/,
    );
  } finally {
    if (previous === undefined) delete process.env.ARK_API_KEY;
    else process.env.ARK_API_KEY = previous;
  }
});

test('obvious text-only or motion-control models are rejected for image-to-video', () => {
  for (const [provider, model] of [
    ['klingai', 'kling-v2.6-t2v'],
    ['klingai', 'kling-v2.6-motion-control'],
    ['bytedance', 'seedance-1-0-lite-t2v-250428'],
    ['alibaba', 'wan2.6-r2v'],
    ['fal', 'minimax-video'],
    ['fal', 'luma-dream-machine/ray-2'],
  ]) {
    assert.throws(() => validateImageToVideoModel(provider, model), /not compatible/);
  }
  assert.doesNotThrow(() => validateImageToVideoModel('alibaba', 'wan2.6-i2v'));
});

test('known provider duration limits fail before submission', () => {
  assert.throws(
    () => validateProviderTaskSettings('fal', 'luma-dream-machine/ray-2/image-to-video', { duration_seconds: 6 }),
    /only 5 or 9/,
  );
  assert.throws(
    () => validateProviderTaskSettings('bytedance', 'seedance-1-5-pro-251215', { duration_seconds: 3 }),
    /between 4 and 12/,
  );
  assert.doesNotThrow(
    () => validateProviderTaskSettings('alibaba', 'wan2.6-i2v-flash', { duration_seconds: 5 }),
  );
});

async function approvedFixture(providerConfig, taskOverrides = {}) {
  const root = await mkdtemp(path.join(os.tmpdir(), 'motion-sticker-pack-provider-'));
  const image = path.join(root, 'sheet.png');
  const layout = path.join(root, 'layout.json');
  const prompt = path.join(root, 'prompts.json');
  const approval = path.join(root, 'job-state.json');
  const config = path.join(root, 'providers.json');
  const task = path.join(root, 'task.json');
  const outputDirectory = path.join(root, 'raw');
  const result = path.join(root, 'result.json');
  const imageBytes = Buffer.from(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=',
    'base64',
  );
  const layoutBytes = Buffer.from(JSON.stringify({
    detected_layout: { columns: 1, rows: 1, count: 1, confidence: 0.99 },
  }));
  await writeFile(image, imageBytes);
  await writeFile(layout, layoutBytes);
  await writeFile(prompt, JSON.stringify({
    detected_layout: { columns: 1, rows: 1, count: 1, confidence: 0.99 },
    grid_video_prompt: 'Fixed camera. The character blinks once in a seamless loop.',
  }));
  await writeFile(approval, JSON.stringify({
    version: 1,
    phase: 'static-approved',
    revision: sha(imageBytes),
    static_image: { sha256: sha(imageBytes) },
    layout: { sha256: sha(layoutBytes) },
    approval: { static_sha256: sha(imageBytes) },
  }));
  await writeFile(config, JSON.stringify({ version: 1, providers: [providerConfig] }));
  await mkdir(outputDirectory);
  await writeFile(task, JSON.stringify({
    version: 1,
    operation: 'image-to-video',
    input_image: image,
    layout_file: layout,
    prompt_file: prompt,
    approval_file: approval,
    output_directory: outputDirectory,
    duration_seconds: 5,
    timeout_seconds: 30,
    poll_interval_ms: 100,
    max_retries: 0,
    max_output_bytes: 1024 * 1024,
    ...taskOverrides,
  }));
  return { config, task, result };
}

test('Seedance, Wan, Kling, and FAL complete request-level mocked I2V calls', async (t) => {
  const cases = [
    {
      name: 'seedance', env: 'ARK_API_KEY', secret: 'seedance-secret', provider: 'bytedance',
      package: '@ai-sdk/bytedance', model: 'seedance-1-5-pro-251215', region: 'china',
      providerOptions: { cameraFixed: true, watermark: false },
      start: url => url === 'https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks',
      status: url => url.endsWith('/contents/generations/tasks/task-seedance'),
      startBody: body => body.resolution === '720p' && body.content.some(item => item.type === 'image_url' && item.image_url.url.startsWith('data:image/png;base64,')),
      startResponse: { id: 'task-seedance' },
      statusResponse: { id: 'task-seedance', status: 'succeeded', content: { video_url: 'https://media.invalid/seedance.mp4' } },
      authorization: 'Bearer seedance-secret',
    },
    {
      name: 'wan', env: 'DASHSCOPE_API_KEY', secret: 'wan-secret', provider: 'alibaba',
      package: '@ai-sdk/alibaba', model: 'wan2.6-i2v-flash', region: 'china',
      providerOptions: { shotType: 'single', watermark: false },
      start: url => url === 'https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis',
      status: url => url === 'https://dashscope.aliyuncs.com/api/v1/tasks/task-wan',
      startBody: body => typeof body.input.img_url === 'string' && body.input.img_url.length > 40 && body.parameters.resolution === '720P',
      startResponse: { output: { task_status: 'PENDING', task_id: 'task-wan' } },
      statusResponse: { output: { task_id: 'task-wan', task_status: 'SUCCEEDED', video_url: 'https://media.invalid/wan.mp4' } },
      authorization: 'Bearer wan-secret',
      expectedWarningCount: 1,
    },
    {
      name: 'kling', env: 'KLINGAI_API_KEY', secret: 'kling-secret', provider: 'klingai',
      package: '@ai-sdk/klingai', model: 'kling-v2.6-i2v', region: 'global',
      providerOptions: { mode: 'std' },
      taskOverrides: { aspect_ratio: '1:1', resolution: '1280x720', fps: 6 },
      start: url => url === 'https://api-singapore.klingai.com/v1/videos/image2video',
      status: url => url === 'https://api-singapore.klingai.com/v1/videos/image2video/task-kling',
      startBody: body => typeof body.image === 'string' && body.image.length > 40 && !('aspect_ratio' in body),
      startResponse: { code: 0, message: 'ok', data: { task_id: 'task-kling', task_status: 'submitted' } },
      statusResponse: { code: 0, message: 'ok', data: { task_id: 'task-kling', task_status: 'succeed', task_result: { videos: [{ id: 'video-kling', url: 'https://media.invalid/kling.mp4', duration: '5' }] } } },
      authorization: 'Bearer kling-secret',
      expectedWarningCount: 3,
    },
    {
      name: 'fal', env: 'FAL_API_KEY', secret: 'fal-secret', provider: 'fal',
      package: '@ai-sdk/fal', model: 'luma-dream-machine/ray-2/image-to-video', region: 'global',
      providerOptions: { loop: true },
      taskOverrides: { aspect_ratio: '1:1', resolution: '1280x720' },
      start: url => url === 'https://queue.fal.run/fal-ai/luma-dream-machine/ray-2/image-to-video',
      status: url => url === 'https://queue.fal.run/fal-ai/luma-dream-machine/ray-2/image-to-video/requests/task-fal',
      startBody: body => body.image_url.startsWith('data:image/png;base64,') && body.resolution === '720p' && body.aspect_ratio === '1:1',
      startResponse: { request_id: 'task-fal', response_url: 'https://queue.fal.run/fal-ai/luma-dream-machine/ray-2/image-to-video/requests/task-fal' },
      statusResponse: { video: { url: 'https://media.invalid/fal.mp4', content_type: 'video/mp4', duration: 5, fps: 24 } },
      authorization: 'Key fal-secret',
    },
  ];

  for (const item of cases) {
    await t.test(item.name, async () => {
      const previousEnv = process.env[item.env];
      process.env[item.env] = item.secret;
      const fixture = await approvedFixture({
        id: item.name,
        driver: 'ai-sdk',
        provider: item.provider,
        package: item.package,
        model: item.model,
        region: item.region,
        enabled: true,
        credentials: { env: [item.env] },
        provider_options: item.providerOptions,
      }, {
        ...item.taskOverrides,
        aspect_ratio: '1:1',
        provider_execution: {
          [item.name]: { duration_seconds: 5, resolution: '720p' },
        },
      });
      const originalFetch = globalThis.fetch;
      let sawStart = false;
      let sawStatus = false;
      globalThis.fetch = async (input, init = {}) => {
        const url = String(input);
        if (url.startsWith('https://media.invalid/')) {
          return new Response(Buffer.from('mock-mp4-bytes'), { status: 200, headers: { 'content-type': 'video/mp4' } });
        }
        const headers = new Headers(init.headers);
        assert.equal(headers.get('authorization'), item.authorization);
        if (item.start(url)) {
          sawStart = true;
          const body = JSON.parse(init.body);
          assert.equal(item.startBody(body), true, JSON.stringify(body));
          return Response.json(item.startResponse);
        }
        if (item.status(url)) {
          sawStatus = true;
          return Response.json(item.statusResponse);
        }
        throw new Error(`unexpected mocked URL for ${item.name}: ${url}`);
      };
      try {
        const report = await execute({
          configFile: fixture.config,
          taskFile: fixture.task,
          providerId: item.name,
          resultFile: fixture.result,
        });
        assert.equal(report.status, 'succeeded');
        assert.equal(report.provider_name, item.provider);
        assert.equal(report.warnings.length, item.expectedWarningCount ?? 0);
        assert.equal(sawStart, true);
        assert.equal(sawStatus, true);
        assert.equal((await readFile(report.output)).toString(), 'mock-mp4-bytes');
        assert.doesNotMatch(JSON.stringify(report), new RegExp(item.secret));
      } finally {
        globalThis.fetch = originalFetch;
        if (previousEnv === undefined) delete process.env[item.env];
        else process.env[item.env] = previousEnv;
      }
    });
  }
});

test('remote failed status is surfaced and no success result is written', async () => {
  const previous = process.env.DASHSCOPE_API_KEY;
  process.env.DASHSCOPE_API_KEY = 'wan-failure-key';
  const fixture = await approvedFixture({
    id: 'wan', driver: 'ai-sdk', provider: 'alibaba', package: '@ai-sdk/alibaba',
    model: 'wan2.6-i2v-flash', region: 'china', enabled: true,
    credentials: { env: ['DASHSCOPE_API_KEY'] }, provider_options: { shotType: 'single' },
  });
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (input) => {
    const url = String(input);
    if (url.endsWith('/video-generation/video-synthesis')) {
      return Response.json({ output: { task_status: 'PENDING', task_id: 'task-failed' } });
    }
    if (url.endsWith('/api/v1/tasks/task-failed')) {
      return Response.json({
        output: { task_id: 'task-failed', task_status: 'FAILED', code: 'InvalidParameter', message: 'bad request' },
      });
    }
    throw new Error(`unexpected mocked URL: ${url}`);
  };
  try {
    await assert.rejects(
      execute({ configFile: fixture.config, taskFile: fixture.task, providerId: 'wan', resultFile: fixture.result }),
      /generation failed.*bad request/i,
    );
    await assert.rejects(readFile(fixture.result), /ENOENT/);
  } finally {
    globalThis.fetch = originalFetch;
    if (previous === undefined) delete process.env.DASHSCOPE_API_KEY;
    else process.env.DASHSCOPE_API_KEY = previous;
  }
});
