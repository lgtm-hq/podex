#!/usr/bin/env bats
# Exercise Docker's dependency-install file boundary without Docker or Bun.

@test "frontend builder has every registered patch before frozen install" {
  export FRONTEND_CONTEXT="${BATS_TEST_DIRNAME}/../../../frontend"
  run python3 - "${BATS_TEST_TMPDIR}" <<'PY'
import fnmatch
import json
import os
from pathlib import Path
import shlex
import shutil
import sys

context = Path(os.environ["FRONTEND_CONTEXT"]).resolve()
builder = Path(sys.argv[1]) / "builder"
builder.mkdir()
# This context uses root-name exclusions only. Fail on unsupported syntax
# rather than silently approximating a future, more complex .dockerignore.
rules = []
for line in (context / ".dockerignore").read_text().splitlines():
    rule = line.strip()
    if not rule or rule.startswith("#"):
        continue
    rule = rule.rstrip("/")
    assert "/" not in rule and not rule.startswith("!"), rule
    rules.append(rule)

def copy_input(source, destination):
    relative = source.relative_to(context)
    if any(fnmatch.fnmatchcase(relative.parts[0], rule) for rule in rules):
        return
    if source.is_dir():
        for child in source.iterdir():
            copy_input(child, destination / child.name)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)

in_builder = False
for line in (context / "Dockerfile").read_text().splitlines():
    words = shlex.split(line.rstrip("\\"), comments=True)
    if not words:
        continue
    if words[0] == "FROM":
        in_builder = words[-1] == "builder"
    if not in_builder:
        continue
    if "bun install --frozen-lockfile" in line:
        break
    if words[0] == "COPY":
        assert not any(word.startswith("--") for word in words[1:]), words
        for source in words[1:-1]:
            target = builder / words[-1]
            if (context / source).is_file():
                target /= Path(source).name
            copy_input(context / source, target)
else:
    raise AssertionError("builder frozen install not found")

manifest = json.loads((builder / "package.json").read_text())
assert (builder / "bun.lock").read_bytes() == (context / "bun.lock").read_bytes()
assert manifest["patchedDependencies"], "expected the Redocly compatibility patch"
for patch in manifest["patchedDependencies"].values():
    installed = builder / patch
    assert installed.is_file(), f"missing frozen-install input: {patch}"
    assert installed.read_bytes() == (context / patch).read_bytes(), patch
    print(f"frozen-install input present: {patch}")
PY
  [ "${status}" -eq 0 ]
}

@test "frontend runtime uses the existing node numeric nonroot identity" {
  run python3 - "${BATS_TEST_DIRNAME}/../../../frontend/Dockerfile" <<'PY'
from pathlib import Path
import shlex
import sys

runtime = []
in_runtime = False
for line in Path(sys.argv[1]).read_text().splitlines():
    words = shlex.split(line.rstrip("\\"), comments=True)
    if not words:
        continue
    if words[0] == "FROM":
        in_runtime = words[-1] == "runtime"
    if in_runtime:
        runtime.append(words)

users = [words[1:] for words in runtime if words[0] == "USER"]
assert users == [["1000:1000"]], f"expected node UID/GID 1000:1000, got {users}"
copies = [words for words in runtime if words[0] == "COPY" and "--from=builder" in words]
assert copies, "expected runtime files from builder"
assert all("--chown=node:node" in words for words in copies), copies
PY
  [ "${status}" -eq 0 ]
}
