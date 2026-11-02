# Scanned PDFs get a searchable copy; originals are never modified

Class readings are often scans with no text layer, which leaves tablet highlights unable to snap to text and Zotero highlights without quoted text. When a PDF attachment has pages without text, the service runs OCR (ocrmypdf) only on those pages, never replacing text that already exists, and stores the result as its own attachment beside the original. The searchable copy is what goes to the tablet; the original scan is left untouched. OCR can be turned off.
