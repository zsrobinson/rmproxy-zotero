# The service is Python; the plugin is TypeScript

rmscene is the only maintained library that reads and writes the tablet's v6 page format, and it is Python, so the service is Python rather than Go (rmfakecloud's and rmapi's language) or TypeScript (rmapi-js). The rmfakecloud sync-client side therefore has to be written in Python rather than reused from rmapi. The Zotero plugin is TypeScript because Zotero plugins run in Zotero's JavaScript environment.
