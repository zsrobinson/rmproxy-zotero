"""Two-way sync between a Zotero library and a reMarkable tablet served by rmfakecloud."""

import os

__version__ = "0.2.0"
# the commit the Docker image was built from (set by the image build), to tell builds apart
REVISION = os.environ.get("RMZOTERO_REVISION", "")[:12] or None
