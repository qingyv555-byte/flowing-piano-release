"""User-facing build identity kept free of source paths and credentials."""

from __future__ import annotations

import os


APP_VERSION = "0.1.0"
IS_BETA = True
BUILD_COMMIT = os.environ.get("AI_PIANO_BUILD_COMMIT", "local-build")
