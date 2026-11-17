# Project instructions for the upstream llmwiki compiler

Generate the wiki in Simplified Chinese. The corpus covers Hong Kong listed internet companies, sector research, company financial reports, earnings presentations, macro policy and share repurchase disclosures.

## Page design

- Create pages only for recurring, decision-useful concepts, companies, products, business segments, metrics, periods, policies, comparisons and sector themes supported by the sources.
- Merge information about the same company or concept across different sources. Do not create one page per input PDF chunk.
- Prefer pages with a clear scope, a concise summary, dated facts and meaningful links to related pages.
- Preserve disagreements between sell-side research, company disclosures and different reporting periods. Label forecasts and opinions with the source and publication date; do not present them as company-reported facts.
- Do not infer causal relationships from co-occurrence. Add cross-links only when the source material or page relationship makes the connection meaningful.
- Do not add facts from outside the supplied source files.

## Evidence and uncertainty

- Cite important factual paragraphs to the supplied Markdown source and line range. The source's `PDF物理页码` heading identifies the original physical PDF page.
- Keep qualifiers, units, currencies, fiscal periods and year-over-year/quarter-over-quarter bases intact.
- Treat OCR text as potentially noisy. Avoid relying on uncertain OCR wording for exact quotes or precise numeric claims; flag such claims for human review.
- If source text is insufficient, state the limitation instead of filling the gap with a guess.

This is an experimental candidate wiki. Pages must remain in the upstream review queue and must not be treated as approved knowledge until citations, numerical claims, links and conflicts have been checked.
