# Items no translator can read: entered by hand, with PDFs found or made on request

Class readings include much that Zotero's translators can't fetch: news sites refuse them, guides and handouts have no page to read, and free copies of papers sit on repository pages rather than behind a DOI. The Claude connector (ADR 0015) handles these without leaving the library half-filled:

- **`create_item`** makes an item from metadata Claude gives, checked against Zotero's own item schema (fetched from api.zotero.org), so it is as valid as one made in Zotero. Duplicates are reused as `add_items` does.
- **`edit_item` can change an item's type** the way Zotero does: fields with the same base field move, others are dropped and listed with their values, and creator roles the new type lacks become its primary role.
- **`attach_pdf`** attaches a PDF from a link to it or to a page that declares one (`citation_pdf_url`), or looks for a free copy itself: translator links, OpenAlex, Unpaywall (when `contact_email` is set) and the landing pages they name.
- **Every "no PDF" says why**: each link tried, with what happened (refused, not found, a page with no PDF link…), and which sources weren't asked.
- **An article page with no PDF can be typeset** into a tablet-sized PDF of its main text, using the same Typst typesetting as tablet editions (ADR 0010). This happens only when asked (`typeset_webpage`), as with tablet editions (ADR 0016), because what counts as "the article" is a judgement call. Images are left out. The PDF is tagged `rmzotero:webpage`.

Requests identify themselves as rmzotero. A site that refuses them is reported as refusing, and its readings are entered by hand with the PDF supplied by the user; the service doesn't present itself as a browser to get past a site's checks.

## Considered Options

- **Scraping page metadata when translators fail**: mostly the same sites refuse both, and hand entry from the citation is more accurate than a guessed one.
- **A headless browser for pages and PDFs**: heavier, and its purpose would largely be getting past sites that refuse automated access.
- **Typesetting every web item automatically**: would put unchecked extractions on the tablet.
