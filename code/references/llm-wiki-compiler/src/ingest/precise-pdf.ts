/**
 * Precise PDF ingestion: run text, image, and screenshot extraction together
 * and merge the results into a single markdown body.
 *
 * The three extractors each produce a fragment:
 * - text:       the PDF text layer (primary content, kept in front)
 * - image:      embedded images understood through the vision provider
 * - screenshot: full-page render + vision, only for pages whose text layer is
 *               empty — avoids re-OCR'ing text the text layer already carries
 *
 * Fragments are merged in that order. This is the "expensive but complete"
 * counterpart to the default rough path, which only runs `getText()`.
 *
 * pdf-parse (and its transitive pdfjs-dist) is imported dynamically so the
 * cost of loading the parser is only paid when a PDF is actually ingested.
 */

import { readFile } from "fs/promises";
import { describeImageContent } from "./image.js";
import { resolveTitle } from "./pdf.js";
import type { IngestedSource } from "./shared.js";

/** Pages with fewer visible characters than this are re-read via screenshot. */
const MIN_PAGE_TEXT_CHARS = 40;

/** Split a "data:<mime>;base64,<payload>" URL into its parts. */
function parseDataUrl(dataUrl: string): { mimeType: string; base64: string } {
  const match = /^data:([^;]+);base64,(.*)$/s.exec(dataUrl);
  if (!match) throw new Error("Unable to parse image data URL from PDF");
  return { mimeType: match[1], base64: match[2] };
}

/**
 * Ingest a PDF with the full extractor pipeline and return the merged result.
 *
 * @param filePath - Absolute or relative path to a .pdf file.
 * @returns An object with the document title and merged text + vision content.
 * @throws When the vision provider is unavailable or the PDF is unparseable.
 */
export default async function ingestPdfPrecise(filePath: string): Promise<IngestedSource> {
  const { PDFParse } = await import("pdf-parse");

  const buffer = await readFile(filePath);
  const parser = new PDFParse({ data: new Uint8Array(buffer) });

  try {
    // Sequential calls are required: pdfjs-dist's LoopbackPort.postMessage uses
    // structuredClone internally; concurrent calls cause a DataCloneError.
    const textResult = await parser.getText();
    const infoResult = await parser.getInfo();
    const title = resolveTitle(filePath, infoResult.info);

    const parts: string[] = [];

    // 1. Text layer (primary content).
    const text = textResult.text.trim();
    if (text) parts.push(text);

    // 2. Embedded images understood through vision.
    const imageResult = await parser.getImage({ imageDataUrl: true, imageBuffer: false });
    const seenImages = new Set<string>();
    for (const page of imageResult.pages) {
      for (const img of page.images) {
        if (seenImages.has(img.name)) continue; // 同一图片对象被多页复用，跳过重复 OCR
        seenImages.add(img.name);
        const { mimeType, base64 } = parseDataUrl(img.dataUrl);
        const description = await describeImageContent(base64, mimeType);
        parts.push(`[图片 p${page.pageNumber} ${img.name}]\n${description}`);
      }
    }

    // 3. Screenshot fallback for pages whose text layer is empty.
    const emptyPages = textResult.pages
      .filter((page) => (page.text?.trim().length ?? 0) < MIN_PAGE_TEXT_CHARS)
      .map((page) => page.num);
    if (emptyPages.length > 0) {
      const shots = await parser.getScreenshot({
        partial: emptyPages,
        imageDataUrl: true,
        imageBuffer: false,
        scale: 2,
      });
      for (const shot of shots.pages) {
        const { mimeType, base64 } = parseDataUrl(shot.dataUrl);
        const description = await describeImageContent(base64, mimeType);
        parts.push(`[整页截图 p${shot.pageNumber}]\n${description}`);
      }
    }

    return { title, content: parts.join("\n\n") };
  } finally {
    await parser.destroy();
  }
}
