# Claude connects to the library through an MCP server inside the service

Claude (via claude.ai, so it works from a phone) gets an MCP endpoint served by the rmzotero service itself, beside the plugin API. The service already holds the library mirror, the files and the exact tablet pages, so Claude can see handwriting as well as highlights, which a stand-alone Zotero MCP server could not.

Claude manages the *library* and never the tablet directly: what's on the tablet keeps following from the library (ADR 0006); the plugin's "send again"/"remove" stay plugin-only recovery actions. It is a plain connector: tools return facts and page images, and Claude does the reading, judging and asking.

- **Adding items** goes through Zotero's own translation-server (a further container), so items match what Zotero itself would create. Existing items are reused rather than duplicated (DOI/ISBN/title match). Freely available PDFs are attached automatically; what couldn't be found is reported in the tool result, with no tags or inbox folders.
- **No built-in workflows** (e.g. syllabus layouts); Claude asks the user how they want things organised.
- **Mutating tools** are marked as such so claude.ai asks for confirmation; nothing deletes items.
- **Handwriting** is offered as rendered page images; nothing is transcribed or saved automatically.
- **Access** is a long secret in the connector URL (an "authless" connector whose URL is the credential), which claude.ai supports without an OAuth server; rotating the secret revokes access.

## Considered Options

- **A community Zotero MCP server**: blind to tablet markup and a second copy of library logic.
- **Explicit send/remove tools for Claude**: a second way to decide what's on the tablet that could disagree with the library.
- **OAuth login**: the standard for claude.ai connectors, but a whole authorization server for a single-user tool.
