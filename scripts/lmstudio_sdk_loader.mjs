#!/usr/bin/env node
/**
 * Load an LM Studio model with advanced SDK-only llama.cpp settings.
 *
 * LM Studio's `lms load` CLI does not currently expose KV-cache quantization
 * or context-checkpoint controls. This helper keeps boot and runtime switching
 * on the official SDK while retaining the normal `lms` path for simple models.
 */

import { existsSync, readdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

function parseArgs(argv) {
  const values = {};
  const flags = new Set();
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    if (["--dry-run", "--mtp", "--flash-attention", "--offload-kv-cache"].includes(token)) {
      flags.add(token);
      continue;
    }
    if (!token.startsWith("--") || index + 1 >= argv.length) {
      throw new Error(`Invalid argument: ${token}`);
    }
    values[token] = argv[index + 1];
    index += 1;
  }
  return { values, flags };
}

function required(values, name) {
  if (!values[name]) throw new Error(`Missing required argument: ${name}`);
  return values[name];
}

function integer(values, name, fallback) {
  if (values[name] === undefined) return fallback;
  const parsed = Number.parseInt(values[name], 10);
  if (!Number.isSafeInteger(parsed) || parsed < 0) throw new Error(`Invalid integer for ${name}`);
  return parsed;
}

function gpuSetting(raw) {
  if (raw === "max" || raw === "off") return { ratio: raw };
  const ratio = Number.parseFloat(raw);
  if (!Number.isFinite(ratio) || ratio < 0 || ratio > 1) throw new Error(`Invalid GPU ratio: ${raw}`);
  return { ratio };
}

function buildRequest(values, flags) {
  const model = required(values, "--model");
  const identifier = required(values, "--identifier");
  const config = {
    contextLength: integer(values, "--context-length", 4096),
    maxParallelPredictions: integer(values, "--parallel", 1),
    useUnifiedKvCache: false,
    gpu: gpuSetting(values["--gpu"] ?? "max"),
    gpuStrictVramCap: true,
    flashAttention: flags.has("--flash-attention"),
    offloadKVCacheToGpu: flags.has("--offload-kv-cache"),
    speculativeDraftMtp: flags.has("--mtp"),
    keepModelInMemory: true,
  };
  const optional = [
    ["--k-cache", "llamaKCacheQuantizationType"],
    ["--v-cache", "llamaVCacheQuantizationType"],
    ["--context-checkpoints", "contextCheckpoints"],
    ["--eval-batch-size", "evalBatchSize"],
    ["--physical-batch-size", "physicalBatchSize"],
  ];
  for (const [argument, field] of optional) {
    if (values[argument] === undefined) continue;
    config[field] = argument.includes("cache") && !argument.includes("checkpoints")
      ? values[argument]
      : integer(values, argument, 0);
  }
  return { model, identifier, config, ttl: integer(values, "--ttl", 0) };
}

function sdkCandidates() {
  const explicit = process.env.LMSTUDIO_SDK_PATH;
  if (explicit) return [explicit];
  const plugins = join(homedir(), ".lmstudio", "extensions", "plugins");
  if (!existsSync(plugins)) return [];
  const candidates = [];
  const pluginDirectories = [];
  const visit = (directory, depth) => {
    pluginDirectories.push(directory);
    if (depth >= 3) return;
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      if (entry.isDirectory()) visit(join(directory, entry.name), depth + 1);
    }
  };
  visit(plugins, 0);
  for (const plugin of pluginDirectories.sort()) {
    for (const entry of ["index.mjs", "index.cjs"]) {
      candidates.push(join(plugin, "node_modules", "@lmstudio", "sdk", "dist", entry));
    }
  }
  return candidates.filter(existsSync);
}

async function loadSdk() {
  const candidates = sdkCandidates();
  if (candidates.length === 0) {
    throw new Error("LM Studio SDK not found under ~/.lmstudio/extensions/plugins; start/update LM Studio first");
  }
  const imported = await import(pathToFileURL(candidates[0]).href);
  const LMStudioClient = imported.LMStudioClient ?? imported.default?.LMStudioClient;
  if (!LMStudioClient) throw new Error(`LMStudioClient export missing from ${candidates[0]}`);
  return LMStudioClient;
}

async function main() {
  const { values, flags } = parseArgs(process.argv.slice(2));
  const request = buildRequest(values, flags);
  if (flags.has("--dry-run")) {
    process.stdout.write(`${JSON.stringify(request)}\n`);
    return;
  }
  const LMStudioClient = await loadSdk();
  const client = new LMStudioClient();
  console.error(`[lmstudio-sdk-loader] loading ${request.identifier} (${request.config.contextLength} context)`);
  await client.llm.load(request.model, {
    identifier: request.identifier,
    config: request.config,
    ...(request.ttl > 0 ? { ttl: request.ttl } : {}),
    verbose: false,
  });
  console.error(`[lmstudio-sdk-loader] loaded ${request.identifier}`);
}

main().catch((error) => {
  console.error(`[lmstudio-sdk-loader] ${error?.stack ?? error}`);
  process.exitCode = 1;
});
