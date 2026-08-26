#!/usr/bin/env node
/** Execute one approved image-to-video request through a bundled AI SDK provider. */

import { createHash } from 'node:crypto';
import { constants as fsConstants } from 'node:fs';
import { access, mkdir, readFile, realpath, stat, writeFile } from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';
import { experimental_generateVideo as generateVideo } from 'ai';

export const ADAPTERS = Object.freeze({
  xai: { package: '@ai-sdk/xai', exportName: 'xai' },
  klingai: { package: '@ai-sdk/klingai', exportName: 'klingai' },
  bytedance: { package: '@ai-sdk/bytedance', exportName: 'byteDance' },
  alibaba: { package: '@ai-sdk/alibaba', exportName: 'alibaba' },
  fal: { package: '@ai-sdk/fal', exportName: 'fal' },
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
    return { available: true, provider: providerName, package: packageName };
  } catch (error) {
    return { available: false, reason: 'provider-import-failed', error: error?.code ?? error?.name ?? 'Error' };
  }
}

async function loadProvider(providerConfig) {
  const status = await checkProvider(providerConfig.provider, providerConfig.package);
  if (!status.available) fail(`AI SDK provider is not executable: ${status.reason}`);
  const adapter = ADAPTERS[providerConfig.provider];
  const module = await import(adapter.package);
  return module[adapter.exportName];
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
    message: String(warning?.message ?? warning?.feature ?? 'provider warning').slice(0, 500),
  }));
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
  const optionStack = [providerConfig.provider_options ?? {}];
  while (optionStack.length) {
    const value = optionStack.pop();
    if (value && typeof value === 'object') {
      for (const [key, child] of Object.entries(value)) {
        if (/(?:api[_-]?key|secret|token|password|credential)/i.test(key)) {
          fail(`provider_options must not contain literal credentials (${key})`);
        }
        optionStack.push(child);
      }
    }
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
  const outputDirectory = task.output_directory;
  if (typeof outputDirectory !== 'string' || !path.isAbsolute(outputDirectory) || path.parse(outputDirectory).root === outputDirectory) {
    fail('output_directory must be an absolute non-root directory');
  }
  await mkdir(outputDirectory, { recursive: true });
  await access(outputDirectory, fsConstants.W_OK);

  const duration = Number(task.duration_seconds ?? 3);
  if (!Number.isFinite(duration) || duration < 1 || duration > 30) fail('duration_seconds must be between 1 and 30');
  const timeoutSeconds = Number(task.timeout_seconds ?? 900);
  if (!Number.isFinite(timeoutSeconds) || timeoutSeconds < 30 || timeoutSeconds > 3600) {
    fail('timeout_seconds must be between 30 and 3600');
  }
  const provider = await loadProvider(providerConfig);
  const options = {
    model: provider.video(providerConfig.model),
    prompt: { image: new Uint8Array(await readFile(image)), text: prompts.grid_video_prompt },
    duration,
    n: 1,
    abortSignal: AbortSignal.timeout(timeoutSeconds * 1000),
  };
  if (task.aspect_ratio && !['source', 'adaptive'].includes(task.aspect_ratio)) options.aspectRatio = task.aspect_ratio;
  if (task.resolution) options.resolution = task.resolution;
  if (task.fps) options.fps = Number(task.fps);
  if (providerConfig.provider_options && Object.keys(providerConfig.provider_options).length) {
    options.providerOptions = { [providerConfig.provider]: providerConfig.provider_options };
  }

  const generated = await generateVideo(options);
  const video = generated.video ?? generated.videos?.[0];
  if (!video?.uint8Array?.byteLength) fail('provider returned no downloadable video bytes');
  const maxBytes = Number(task.max_output_bytes ?? 200 * 1024 * 1024);
  if (!Number.isSafeInteger(maxBytes) || maxBytes < 1024 || video.uint8Array.byteLength > maxBytes) {
    fail(`generated video exceeds max_output_bytes (${video.uint8Array.byteLength} bytes)`);
  }
  const output = path.join(outputDirectory, `generated-video${extensionFor(video.mediaType)}`);
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
    warnings: safeWarnings(generated.warnings),
  };
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
