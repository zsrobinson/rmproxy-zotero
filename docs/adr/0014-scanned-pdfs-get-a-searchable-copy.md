# Scanned PDFs get a searchable copy; originals are never modified

Class readings are often scans with no text layer, which leaves tablet highlights unable to snap to text and Zotero highlights without quoted text. When a PDF attachment has pages without text, the service runs OCR (ocrmypdf) only on those pages, never replacing text that already exists, and stores the result as its own attachment beside the original. The searchable copy is what goes to the tablet; the original scan is left untouched. OCR can be turned off.

A PDF counts as a scan only when at least a quarter of its pages have no text and are mostly covered by a picture. Covers, blank pages and photo plates in a typeset book don't make it a scan (the first version treated any page without text as a scan, and made needless copies of books).
