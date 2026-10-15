#!/usr/bin/env bash
# Write contract/openapi.json: the bundled contract of this repository at the tbo_contracts ref in
# contract/source.json. Run it after changing that ref; CI fails when the vendored file is stale.
#
#   scripts/vendor_contract.sh [path to a tbo_contracts checkout]     (default: $TBO_CONTRACTS_DIR, else a clone)
set -euo pipefail
cd "$(dirname "$0")/.."
read -r repo ref name < <(uv run --no-project python -c 'import json; d = json.load(open("contract/source.json")); print(d["repository"], d["ref"], d["name"])')
dir="${1:-${TBO_CONTRACTS_DIR:-}}"
if [[ -z "$dir" ]]; then
  dir="$(mktemp -d)"
  trap 'rm -rf "$dir"' EXIT
  git clone -q "https://github.com/$repo" "$dir"
  git -C "$dir" -c advice.detachedHead=false checkout -q "$ref"
fi
# Never move someone's checkout: a given directory must already be at the ref.
want="$(git -C "$dir" rev-parse --verify -q "$ref^{commit}" || true)"
[[ -n "$want" && "$want" == "$(git -C "$dir" rev-parse HEAD)" ]] || {
  echo "vendor_contract: $dir is not at $ref (check it out there first)" >&2
  exit 2
}
uv run python -m tbo_platform_kit.contract bundle "$dir" "$name" -o contract/openapi.json
echo "contract/openapi.json is $name at $ref"
