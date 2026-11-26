/**
 * Image ingestion module using LLM vision capabilities.
 *
 * Reads a local image file, encodes it as base64, and sends it to the
 * configured LLM provider's vision endpoint for OCR-plus-description
 * extraction. The anthropic provider goes through the Anthropic SDK; every
 * other provider that exposes an OpenAI-compatible vision endpoint (qwen-vl,
 * glm-4v, minimax-vl, doubao-vision, ...) is reached through the shared
 * provider abstraction.
 *
 * Throws a clear error when the provider does not support vision, rather
 * than falling back silently.
 */

import { readFile } from "fs/promises";
import path from "path";
import Anthropic from "@anthropic-ai/sdk";
import { buildAnthropicClientOptions } from "../providers/anthropic.js";
import { DEFAULT_PROVIDER, IMAGE_DESCRIBE_MAX_TOKENS, PROVIDER_MODELS, normalizeProviderName } from "../utils/constants.js";
import { resolveAnthropicAuthFromEnv, resolveAnthropicBaseURLFromEnv, resolveAnthropicModelFromEnv } from "../utils/claude-settings.js";
import { buildProvider } from "../utils/provider.js";
import { titleFromFilename, type IngestedSource } from "./shared.js";

/** Mime types supported by vision ingest. */
type AnthropicImageMediaType = "image/jpeg" | "image/png" | "image/gif" | "image/webp";

const EXTENSION_TO_MIME: Record<string, AnthropicImageMediaType> = {
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".png": "image/png",
  ".gif": "image/gif",
  ".webp": "image/webp",
};

/** Return the MIME type for an image file, or throw on unknown extension. */
function mimeTypeForExtension(ext: string): AnthropicImageMediaType {
  const mimeType = EXTENSION_TO_MIME[ext.toLowerCase()];
  if (!mimeType) {
    throw new Error(
      `Unsupported image extension "${ext}". Supported: ${Object.keys(EXTENSION_TO_MIME).join(", ")}`,
    );
  }
  return mimeType;
}

/** Shared prompt for vision OCR + description, used by both provider paths. */
const VISION_PROMPT =
  "Extract and transcribe all text visible in this image. Then provide a detailed description of any non-text visual content. Format your response as markdown.";

/** Build an Anthropic client from the current environment config. */
function buildClient(): Anthropic {
  const baseURL = resolveAnthropicBaseURLFromEnv();
  const auth = resolveAnthropicAuthFromEnv();
  return new Anthropic(buildAnthropicClientOptions({ baseURL, ...auth }));
}

/** Send an image to Anthropic vision and return the extracted description. */
async function describeImageWithVision(
  client: Anthropic,
  model: string,
  imageData: string,
  mimeType: AnthropicImageMediaType,
): Promise<string> {
  const response = await client.messages.create({
    model,
    max_tokens: IMAGE_DESCRIBE_MAX_TOKENS,
    messages: [
      {
        role: "user",
        content: [
          {
            type: "image",
            source: { type: "base64", media_type: mimeType, data: imageData },
          },
          {
            type: "text",
            text: VISION_PROMPT,
          },
        ],
      },
    ],
  });

  const textBlock = response.content.find((block) => block.type === "text");
  return textBlock?.type === "text" ? textBlock.text : "";
}

/**
 * Ingest a local image file using LLM vision for OCR and description.
 *
 * Routes to Anthropic vision for the anthropic provider, and to the provider
 * abstraction's describeImage for OpenAI-compatible providers (openai, minimax,
 * atlascloud, ...). Throws when the active provider has no image support.
 *
 * @param filePath - Absolute or relative path to an image file.
 * @returns An object with a title derived from the filename and the extracted content.
 * @throws When the provider does not support vision or on read/API failure.
 */
export default async function ingestImage(filePath: string): Promise<IngestedSource> {
  const ext = path.extname(filePath).toLowerCase();
  const mimeType = mimeTypeForExtension(ext);
  const imageBuffer = await readFile(filePath);
  const imageData = imageBuffer.toString("base64");
  const title = titleFromFilename(filePath);

  const providerName = normalizeProviderName(process.env.LLMWIKI_PROVIDER ?? DEFAULT_PROVIDER);

  if (providerName === "anthropic") {
    const client = buildClient();
    const model = resolveAnthropicModelFromEnv() ?? PROVIDER_MODELS.anthropic;
    const content = await describeImageWithVision(client, model, imageData, mimeType);
    return { title, content };
  }

  const provider = buildProvider(providerName);
  if (!provider.describeImage) {
    throw new Error(
      `Image ingest requires a vision-capable provider. ` +
        `"${providerName}" has no image support. ` +
        `Use an OpenAI-compatible vision provider (e.g. LLMWIKI_PROVIDER=openai with LLMWIKI_MODEL=qwen-vl-max) or anthropic.`,
    );
  }
  const content = await provider.describeImage(mimeType, imageData, VISION_PROMPT);
  return { title, content };
}
