#!/usr/bin/env node
/** Execute one approved image-to-video request through a bundled AI SDK provider. */

import { createHash } from 'node:crypto';
import { constants as fsConstants } from 'node:fs';
import { access, mkdir, readFile, realpath, stat, writeFile } from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';
import { createDownload, experimental_generateVideo as generateVideo } from 'ai';

export const ADAPTERS = Object.freeze({
  xai: {
    package: '@ai-sdk/xai', exportName: 'xai', factoryExport: 'createXai', defaultRegion: 'global', regions: { global: {} },
    authEnvSets: [['XAI_API_KEY']],
  },
  klingai: {
    package: '@ai-sdk/klingai', exportName: 'klingai', factoryExport: 'createKlingAI', defaultRegion: 'global', regions: { global: {} },
    authEnvSets: [['KLINGAI_API_KEY'], ['KLINGAI_ACCESS_KEY', 'KLINGAI_SECRET_KEY']],
  },
  bytedance: {
    package: '@ai-sdk/bytedance', exportName: 'byteDance', factoryExport: 'createByteDance', defaultRegion: 'international',
    regions: {
      international: {},
      china: { baseURL: 'https://ark.cn-beijing.volces.com/api/v3' },
    },
    authEnvSets: [['ARK_API_KEY']],
  },
  alibaba: {
    package: '@ai-sdk/alibaba', exportName: 'alibaba', factoryExport: 'createAlibaba', defaultRegion: 'international',
    regions: {
      international: {},
      china: {
        baseURL: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
        videoBaseURL: 'https://dashscope.aliyuncs.com',
      },
    },
    authEnvSets: [['ALIBABA_API_KEY'], ['DASHSCOPE_API_KEY']],
  },
  fal: {
    package: '@ai-sdk/fal', exportName: 'fal', factoryExport: 'createFal', defaultRegion: 'global', regions: { global: {} },
    authEnvSets: [['FAL_API_KEY'], ['FAL_KEY']],
  },
});

const PROVIDER_OPTION_FIELDS = Object.freeze({
  xai: new Set(['mode', 'videoUrl', 'referenceImageUrls', 'referenceVoiceIds', 'resolution', 'user']),
  klingai: new Set([
    'mode', 'negativePrompt', 'sound', 'cfgScale', 'cameraControl', 'imageTail', 'staticMask',
    'dynamicMasks', 'multiShot', 'shotType', 'multiPrompt', 'elementList', 'voiceList',
    'watermarkEnabled', 'videoUrl', 'characterOrientation', 'keepOriginalSound',
  ]),
  bytedance: new Set([
    'watermark', 'generateAudio', 'cameraFixed', 'returnLastFrame', 'serviceTier', 'draft',
    'lastFrameImage', 'referenceImages', 'referenceVideos', 'referenceAudio',
  ]),
  alibaba: new Set([
    'negativePrompt', 'audioUrl', 'promptExtend', 'shotType', 'watermark', 'audio',
    'referenceUrls', 'media', 'ratio',
  ]),
  fal: new Set(['loop', 'motionStrength', 'resolution', 'negativePrompt', 'promptOptimizer']),
});

function fail(message) {
  throw new Error(message);
}

function parseArgs(argv) {
  const result = {};
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (!key.startsWith('--')) fail(`unexpected argument: ${key}`);
    const value = argv[index + 1];
    if (!value || value.startsWith('--')) fail(`missing value for ${key}`);
    result[key.slice(2)] = value;
    index += 1;
  }
  return result;
}

async function readJson(file, label) {
  let value;
  try {
    value = JSON.parse(await readFile(file, 'utf8'));
  } catch (error) {
    fail(`cannot read valid ${label} JSON from ${file}: ${error.message}`);
  }
  if (!value || Array.isArray(value) || typeof value !== 'object') fail(`${label} must be a JSON object`);
  return value;
}

async function sha256(file) {
  return createHash('sha256').update(await readFile(file)).digest('hex');
}

async function requireAbsoluteFile(value, label) {
  if (typeof value !== 'string' || !path.isAbsolute(value)) fail(`${label} must be an absolute path`);
  const resolved = await realpath(value);
  const info = await stat(resolved);
  if (!info.isFile()) fail(`${label} is not a file`);
  return resolved;
}

function detectedLayout(value) {
  const item = value?.detected_layout ?? value;
  const columns = Number(item?.columns);
  const rows = Number(item?.rows);
  const count = Number(item?.count ?? columns * rows);
  if (!Number.isInteger(columns) || !Number.isInteger(rows) || columns < 1 || rows < 1 || count !== columns * rows) {
    fail('layout must contain positive columns and rows with count = columns * rows');
  }
  const confidence = Number(item?.confidence);
  if (!Number.isFinite(confidence) || confidence < 0.75 || confidence > 1) {
    fail('layout confidence must be at least 0.75 or use a confirmed manual override');
  }
  return { columns, rows, count };
}

export async function checkProvider(providerName, packageName) {
  const adapter = ADAPTERS[providerName];
  if (!adapter) return { available: false, reason: 'unsupported-provider' };
  if (packageName !== adapter.package) return { available: false, reason: 'package-provider-mismatch' };
  try {
    const module = await import(adapter.package);
    const provider = module[adapter.exportName];
    if (!provider || typeof provider.video !== 'function') {
      return { available: false, reason: 'provider-has-no-video-factory' };
    }
    if (typeof module[adapter.factoryExport] !== 'function') {
      return { available: false, reason: 'provider-has-no-configurable-factory' };
    }
    return {
      available: true,
      provider: providerName,
      package: packageName,
      regions: Object.keys(adapter.regions),
    };
  } catch (error) {
    return { available: false, reason: 'provider-import-failed', error: error?.code ?? error?.name ?? 'Error' };
  }
}

function requiredEnvironment(providerConfig) {
  const names = providerConfig.credentials?.env ?? [];
  const values = names.map((name) => process.env[name]?.trim());
  const missing = names.filter((_, index) => !values[index]);
  if (missing.length) fail(`missing required credential environment: ${missing.join(', ')}`);
  return Object.fromEntries(names.map((name, index) => [name, values[index]]));
}

export function providerFactorySettings(providerConfig) {
  const adapter = ADAPTERS[providerConfig.provider];
  if (!adapter) fail(`unsupported AI SDK provider: ${providerConfig.provider}`);
  const region = providerConfig.region ?? adapter.defaultRegion;
  const regionSettings = adapter.regions[region];
  if (!regionSettings) {
    fail(`unsupported region ${region} for provider ${providerConfig.provider}`);
  }
  const declaredNames = providerConfig.credentials?.env ?? [];
  const validAuthSet = adapter.authEnvSets.some((expected) =>
    expected.length === declaredNames.length && expected.every((name, index) => name === declaredNames[index]));
  if (!validAuthSet) {
    fail(`unsupported credential environment set for provider ${providerConfig.provider}`);
  }
  const credentials = requiredEnvironment(providerConfig);
  const names = Object.keys(credentials);
  const settings = { ...regionSettings };
  if (providerConfig.provider === 'klingai' && names.includes('KLINGAI_ACCESS_KEY')) {
    settings.accessKey = credentials.KLINGAI_ACCESS_KEY;
    settings.secretKey = credentials.KLINGAI_SECRET_KEY;
  } else {
    settings.apiKey = credentials[names[0]];
  }
  return settings;
}

async function loadProvider(providerConfig) {
  const status = await checkProvider(providerConfig.provider, providerConfig.package);
  if (!status.available) fail(`AI SDK provider is not executable: ${status.reason}`);
  const adapter = ADAPTERS[providerConfig.provider];
  const module = await import(adapter.package);
  return module[adapter.factoryExport](providerFactorySettings(providerConfig));
}

export function validateImageToVideoModel(providerName, model) {
  const normalized = String(model).toLowerCase();
  const incompatible =
    (providerName === 'klingai' && (normalized.includes('-t2v') || normalized.includes('motion-control'))) ||
    (providerName === 'bytedance' && normalized.includes('-t2v-')) ||
    (providerName === 'alibaba' && (normalized.includes('-t2v') || normalized.includes('-r2v'))) ||
    (providerName === 'fal' && [
      'minimax-video',
      'minimax-video-01',
      'luma-dream-machine',
      'luma-ray-2',
      'luma-ray-2-flash',
      'luma-dream-machine/ray-2',
    ].includes(normalized));
  if (incompatible) fail(`model ${model} is not compatible with image-to-video`);
}

export function validateProviderTaskSettings(providerName, model, task, providerId = undefined) {
  const duration = Number(task.provider_execution?.[providerId]?.duration_seconds ?? task.duration_seconds ?? 5);
  if (!Number.isInteger(duration)) fail(`provider ${providerName} requires an integer duration_seconds`);
  const normalized = String(model).toLowerCase();
  if (providerName === 'bytedance') {
    const minimum = normalized.includes('seedance-1-5') ? 4 : 2;
    if (duration < minimum || duration > 12) {
      fail(`model ${model} requires duration_seconds between ${minimum} and 12`);
    }
  }
  if (providerName === 'alibaba' && normalized.startsWith('wan2.6-i2v') && (duration < 2 || duration > 15)) {
    fail(`model ${model} requires duration_seconds between 2 and 15`);
  }
  if (
    providerName === 'fal' &&
    normalized === 'luma-dream-machine/ray-2/image-to-video' &&
    ![5, 9].includes(duration)
  ) {
    fail(`model ${model} supports only 5 or 9 second durations`);
  }
}

async function verifyApproval(task) {
  const image = await requireAbsoluteFile(task.input_image, 'input_image');
  const maxInputBytes = Number(task.max_input_image_bytes ?? 25 * 1024 * 1024);
  const imageInfo = await stat(image);
  if (!Number.isSafeInteger(maxInputBytes) || maxInputBytes < 1024 || imageInfo.size > maxInputBytes) {
    fail(`input image exceeds max_input_image_bytes (${imageInfo.size} bytes)`);
  }
  const layoutFile = await requireAbsoluteFile(task.layout_file, 'layout_file');
  const approvalFile = await requireAbsoluteFile(task.approval_file, 'approval_file');
  const state = await readJson(approvalFile, 'approval state');
  if (state.version !== 1 || state.phase !== 'static-approved' || !state.approval) {
    fail('the current static revision is not approved');
  }
  const imageHash = await sha256(image);
  const layoutHash = await sha256(layoutFile);
  if (state.static_image?.sha256 !== imageHash || state.approval?.static_sha256 !== imageHash) {
    fail('input_image does not match the approved static revision');
  }
  if (state.layout?.sha256 !== layoutHash) fail('layout_file does not match the approved static revision');
  return { image, layoutFile, revision: state.revision };
}

function extensionFor(mediaType) {
  const normalized = String(mediaType ?? '').toLowerCase().split(';', 1)[0];
  const known = { 'video/mp4': '.mp4', 'video/webm': '.webm', 'video/quicktime': '.mov' };
  const extension = known[normalized];
  if (!extension) fail(`unsupported generated video media type: ${mediaType ?? 'missing'}`);
  return extension;
}

function safeWarnings(warnings) {
  return (warnings ?? []).map((warning) => ({
    type: String(warning?.type ?? 'warning'),
    message: String(warning?.message ?? warning?.details ?? warning?.feature ?? 'provider warning').slice(0, 500),
  }));
}

function requestIdFromMetadata(providerName, metadata) {
  const providerMetadata = metadata?.[providerName];
  if (!providerMetadata || typeof providerMetadata !== 'object') return undefined;
  for (const key of ['taskId', 'requestId', 'request_id']) {
    const value = providerMetadata[key];
    if (typeof value === 'string' && value.length > 0 && value.length <= 256) return value;
  }
  return undefined;
}

function tierResolution(providerName, tier, aspectRatio = '1:1') {
  if (!['480p', '720p'].includes(tier)) fail(`unsupported resolution tier: ${tier}`);
  const portrait = aspectRatio === '9:16' || aspectRatio === '3:4';
  const square = aspectRatio === '1:1';
  if (tier === '720p') {
    if (square) return '960x960';
    if (aspectRatio === '4:3') return providerName === 'alibaba' ? '1088x832' : '1112x834';
    if (aspectRatio === '3:4') return providerName === 'alibaba' ? '832x1088' : '834x1112';
    return portrait ? '720x1280' : '1280x720';
  }
  if (square) return '624x624';
  return portrait ? '480x832' : '832x480';
}

export async function execute({ configFile, taskFile, providerId, resultFile }) {
  const config = await readJson(configFile, 'provider config');
  const task = await readJson(taskFile, 'video task');
  if (config.version !== 1 || task.version !== 1) fail('config and task versions must both be 1');
  if (task.operation !== 'image-to-video') fail('video gateway supports only image-to-video');
  const providerConfig = config.providers?.find((item) => item.id === providerId);
  if (!providerConfig || providerConfig.driver !== 'ai-sdk' || providerConfig.enabled !== true) {
    fail(`provider ${providerId} is not an enabled ai-sdk provider`);
  }
  if (typeof providerConfig.model !== 'string' || !providerConfig.model.trim() || /replace-with|example/i.test(providerConfig.model)) {
    fail(`provider ${providerId} has no concrete model id`);
  }
  validateImageToVideoModel(providerConfig.provider, providerConfig.model);
  validateProviderTaskSettings(providerConfig.provider, providerConfig.model, task, providerId);
  const optionStack = [providerConfig.provider_options ?? {}];
  while (optionStack.length) {
    const value = optionStack.pop();
    if (value && typeof value === 'object') {
      for (const [key, child] of Object.entries(value)) {
        if (/(?:api[_-]?key|secret|token|password|credential|authorization|auth(?:entication)?)/i.test(key)) {
          fail(`provider_options must not contain literal credentials (${key})`);
        }
        optionStack.push(child);
      }
    }
  }
  const allowedOptions = PROVIDER_OPTION_FIELDS[providerConfig.provider];
  const unknownOptions = Object.keys(providerConfig.provider_options ?? {}).filter((key) => !allowedOptions.has(key));
  if (unknownOptions.length) {
    fail(`unsupported provider_options for ${providerConfig.provider}: ${unknownOptions.sort().join(', ')}`);
  }
  const { image, layoutFile, revision } = await verifyApproval(task);
  const promptFile = await requireAbsoluteFile(task.prompt_file, 'prompt_file');
  const prompts = await readJson(promptFile, 'prompt');
  const layout = detectedLayout(await readJson(layoutFile, 'layout'));
  const promptLayout = detectedLayout(prompts);
  if (JSON.stringify(layout) !== JSON.stringify(promptLayout)) fail('prompt layout differs from the approved detected layout');
  if (typeof prompts.grid_video_prompt !== 'string' || !prompts.grid_video_prompt.trim()) {
    fail('prompt_file is missing grid_video_prompt');
  }
  if (prompts.grid_video_prompt.length > 20000) fail('grid_video_prompt exceeds 20000 characters');
  const providerInputValue = task.provider_input_images?.[providerId] ?? task.input_image;
  const providerInput = await requireAbsoluteFile(providerInputValue, `provider_input_images.${providerId}`);
  const keyColor = task.provider_key_colors?.[providerId] ?? task.key_color ?? '#00FF00';
  if (typeof keyColor !== 'string' || !/^#[0-9A-Fa-f]{6}$/.test(keyColor)) {
    fail(`provider key color for ${providerId} must use #RRGGBB`);
  }
  const screenPrompt = task.allow_key_background
    ? `${prompts.grid_video_prompt}\nHard screen contract: keep every empty pixel, canvas corner, and grid gutter exactly ${keyColor.toUpperCase()} in every frame. No gradient, texture, checkerboard, shadow backdrop, or color shift.`
    : prompts.grid_video_prompt;
  const outputDirectory = task.output_directory;
  if (typeof outputDirectory !== 'string' || !path.isAbsolute(outputDirectory) || path.parse(outputDirectory).root === outputDirectory) {
    fail('output_directory must be an absolute non-root directory');
  }
  await mkdir(outputDirectory, { recursive: true });
  await access(outputDirectory, fsConstants.W_OK);

  const execution = task.provider_execution?.[providerId] ?? {};
  const duration = Number(execution.duration_seconds ?? task.provider_duration_seconds?.[providerId] ?? task.duration_seconds ?? 5);
  if (!Number.isFinite(duration) || duration < 1 || duration > 30) fail('duration_seconds must be between 1 and 30');
  const timeoutSeconds = Number(task.timeout_seconds ?? 900);
  if (!Number.isFinite(timeoutSeconds) || timeoutSeconds < 30 || timeoutSeconds > 3600) {
    fail('timeout_seconds must be between 30 and 3600');
  }
  const pollIntervalMs = Number(task.poll_interval_ms ?? 5000);
  if (!Number.isInteger(pollIntervalMs) || pollIntervalMs < 100 || pollIntervalMs > 60000) {
    fail('poll_interval_ms must be an integer between 100 and 60000');
  }
  const maxRetries = Number(task.max_retries ?? 0);
  if (!Number.isInteger(maxRetries) || maxRetries < 0 || maxRetries > 3) {
    fail('max_retries must be an integer between 0 and 3');
  }
  const maxBytes = Number(task.max_output_bytes ?? 200 * 1024 * 1024);
  if (!Number.isSafeInteger(maxBytes) || maxBytes < 1024) fail('max_output_bytes must be at least 1024');
  const provider = await loadProvider(providerConfig);
  const preflightWarnings = [];
  const providerOptionValues = { ...(providerConfig.provider_options ?? {}) };
  const options = {
    model: provider.video(providerConfig.model),
    prompt: { image: new Uint8Array(await readFile(providerInput)), text: screenPrompt },
    duration,
    n: 1,
    maxRetries,
    abortSignal: AbortSignal.timeout(timeoutSeconds * 1000),
    poll: { intervalMs: pollIntervalMs, timeoutMs: timeoutSeconds * 1000 },
    download: createDownload({ maxBytes }),
  };
  if (task.aspect_ratio && task.aspect_ratio !== 'source') {
    if (['xai', 'bytedance', 'fal'].includes(providerConfig.provider)) {
      options.aspectRatio = task.aspect_ratio;
    } else {
      preflightWarnings.push({
        type: 'unsupported',
        message: `${providerConfig.provider} image-to-video derives aspect ratio from the input image; task aspect_ratio was ignored`,
      });
    }
  }
  const requestedResolution = execution.resolution ?? task.resolution;
  if (requestedResolution) {
    if (requestedResolution.endsWith('p') && providerConfig.provider === 'xai') {
      providerOptionValues.resolution = requestedResolution;
    } else if (providerConfig.provider === 'fal') {
      const shortSide = requestedResolution.endsWith('p')
        ? Number(requestedResolution.slice(0, -1))
        : Math.min(...requestedResolution.split('x').map(Number));
      if (![540, 720, 1080].includes(shortSide)) {
        fail(`FAL resolution ${requestedResolution} cannot be mapped to 540p, 720p, or 1080p`);
      }
      providerOptionValues.resolution = `${shortSide}p`;
    } else if (providerConfig.provider === 'klingai') {
      preflightWarnings.push({
        type: 'unsupported',
        message: 'Kling image-to-video does not support a requested resolution; task resolution was ignored',
      });
    } else {
      options.resolution = requestedResolution.endsWith('p')
        ? tierResolution(providerConfig.provider, requestedResolution, task.aspect_ratio)
        : requestedResolution;
    }
  }
  if (task.fps) {
    preflightWarnings.push({
      type: 'unsupported',
      message: `${providerConfig.provider} controls source-video FPS; task fps is reserved for local sticker post-processing`,
    });
  }
  if (Object.keys(providerOptionValues).length) {
    options.providerOptions = { [providerConfig.provider]: providerOptionValues };
  }

  const generated = await generateVideo(options);
  const video = generated.video ?? generated.videos?.[0];
  if (!video?.uint8Array?.byteLength) fail('provider returned no downloadable video bytes');
  if (video.uint8Array.byteLength > maxBytes) {
    fail(`generated video exceeds max_output_bytes (${video.uint8Array.byteLength} bytes)`);
  }
  // Bind the downloaded artifact to the result file. Provider-only filenames
  // collide when a later route attempt shares the same output directory.
  const resultStem = path.basename(resultFile, path.extname(resultFile)).replace(/[^a-zA-Z0-9._-]/g, '-');
  const output = path.join(outputDirectory, `generated-video-${resultStem}${extensionFor(video.mediaType)}`);
  await writeFile(output, video.uint8Array, { flag: 'wx' });
  const report = {
    version: 1,
    status: 'succeeded',
    provider: providerId,
    provider_name: providerConfig.provider,
    model: providerConfig.model,
    approved_revision: revision,
    output,
    media_type: video.mediaType,
    byte_length: video.uint8Array.byteLength,
    has_alpha: video.mediaType === 'video/webm' ? null : false,
    warnings: [...preflightWarnings, ...safeWarnings(generated.warnings)],
  };
  const requestId = requestIdFromMetadata(providerConfig.provider, generated.providerMetadata);
  if (requestId) report.request_id = requestId;
  await writeFile(resultFile, `${JSON.stringify(report, null, 2)}\n`, { flag: 'wx' });
  return report;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args['check-provider']) {
    const status = await checkProvider(args['check-provider'], args.package);
    process.stdout.write(`${JSON.stringify(status)}\n`);
    if (!status.available) process.exitCode = 1;
    return;
  }
  for (const required of ['config', 'task', 'provider-id', 'output']) {
    if (!args[required]) fail(`--${required} is required`);
  }
  const report = await execute({
    configFile: path.resolve(args.config),
    taskFile: path.resolve(args.task),
    providerId: args['provider-id'],
    resultFile: path.resolve(args.output),
  });
  process.stdout.write(`${JSON.stringify(report)}\n`);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) {
  main().catch((error) => {
    process.stderr.write(`video gateway failed: ${error.message}\n`);
    process.exitCode = 1;
  });
}
