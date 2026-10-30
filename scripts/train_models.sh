#!/usr/bin/env bash
# Train / evaluate ML models from labelled training samples (can be run as a one-off job).
set -euo pipefail
exec python -m backend.ml.train "$@"
