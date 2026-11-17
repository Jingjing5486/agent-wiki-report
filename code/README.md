# Agent Wiki project code

This folder contains the project-owned SearchWiki and wiki-demo applications, plus the RAPTOR experiment driver and its upstream implementation snapshot (MIT license retained). Runtime outputs, prepared source corpora, PDF files, virtual environments, API-key files, and local run logs are intentionally excluded. The llm-wiki-compiler reference remains available at https://github.com/atomicstrata/llm-wiki-compiler.

## Layout

- `apps/searchwiki-poc/`: SearchWiki pipeline, prompts, configuration templates, tests, and project docs.
- `apps/wiki-demo/`: Wiki generation demo, prompts, tools, configuration templates, tests, and project docs.
- `references/raptor-data-demo/`: RAPTOR experiment runner and tree renderer. It expects the prepared corpus and API credentials to be supplied locally.
- `references/raptor/`: Upstream RAPTOR Python source with its license and dependency metadata.

Copy `.env.example` to `.env` and fill credentials locally before running a demo. Never commit `.env`.
