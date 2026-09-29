# SPDX-License-Identifier: AGPL-3.0-or-later
"""Layered file-sorting tagger (``lam tag``).

Scan and plan only. Moving bytes is ``lam actions run --apply``, which this
package never calls.
"""

from lam.tag.errors import TagError
from lam.tag.plan import run_tag_plan
from lam.tag.scan import run_tag_scan

__all__ = ["TagError", "run_tag_plan", "run_tag_scan"]
