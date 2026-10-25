#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
script_dir="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
exec python3 "${script_dir}/apply_encfs.py" "$@"
