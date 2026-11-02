# Claude connects to the library through an MCP server inside the service

Claude (via claude.ai, so it works from a phone) gets an MCP endpoint served by the rmzotero service itself, beside the plugin API. The service already holds the library mirror, the files and the exact tablet pages, so Claude can read handwriting as well as highlights, which a stand-alone Zotero MCP server could not.

Claude manages the *library* — creating items, filing them into collections, adding notes and comments — and never the tablet directly: what's on the tablet keeps following from the library (ADR 0006). The plugin's "send again"/"remove" stay plugin-only recovery actions. Because claude.ai's custom connectors require it, the endpoint is protected by an OAuth login served by the service.

## Considered Options

- **A community Zotero MCP server**: no setup in the service, but blind to tablet markup and a second copy of library logic.
- **Explicit send/remove tools for Claude**: a second way to decide what's on the tablet that could disagree with the library.
