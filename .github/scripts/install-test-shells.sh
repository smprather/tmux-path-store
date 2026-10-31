#!/usr/bin/env bash
# Install pinned test runtimes under the runner's temporary directory.
set -euo pipefail

: "${RUNNER_TEMP:?RUNNER_TEMP must point to a writable temporary directory}"
: "${GITHUB_PATH:?GITHUB_PATH must point to the CI path export file}"

if [[ $(uname -sm) != "Linux x86_64" ]]; then
    printf '%s\n' 'These pinned test artifacts require Linux x86_64.' >&2
    exit 1
fi

runtime_root=$(mktemp -d "$RUNNER_TEMP/tps-shells.XXXXXX")

download() {
    local url=$1 destination=$2 expected_sha256=$3
    curl --fail --location --retry 3 --silent --show-error "$url" --output "$destination"
    printf '%s  %s\n' "$expected_sha256" "$destination" | sha256sum --check --status
}

# Official Nushell release, also exercised locally during integration development.
download \
    'https://github.com/nushell/nushell/releases/download/0.106.1/nu-0.106.1-x86_64-unknown-linux-gnu.tar.gz' \
    "$runtime_root/nu.tar.gz" \
    '4f0688c71a36ba48a253e10876b5b101c3343f4a4deb19c446906b4572c340c9'
mkdir "$runtime_root/nu"
tar -xzf "$runtime_root/nu.tar.gz" -C "$runtime_root/nu" --strip-components=1 \
    nu-0.106.1-x86_64-unknown-linux-gnu/nu
"$runtime_root/nu/nu" --version
printf '%s\n' "$runtime_root/nu" >> "$GITHUB_PATH"

# GitHub's Ubuntu images normally provide PowerShell. Preserve that runtime when available.
if command -v pwsh >/dev/null 2>&1; then
    # PowerShell expands this expression.
    # shellcheck disable=SC2016
    pwsh -NoLogo -NoProfile -NonInteractive -Command '$PSVersionTable.PSVersion.ToString()'
else
    download \
        'https://github.com/PowerShell/PowerShell/releases/download/v7.5.2/powershell-7.5.2-linux-x64.tar.gz' \
        "$runtime_root/pwsh.tar.gz" \
        '8fa9584f6f95d29ca1466c4397ac39c371373d6581c12dfae9ebd53c06d77664'
    mkdir "$runtime_root/pwsh"
    tar -xzf "$runtime_root/pwsh.tar.gz" -C "$runtime_root/pwsh"
    chmod u+x "$runtime_root/pwsh/pwsh"
    # PowerShell expands this expression.
    # shellcheck disable=SC2016
    "$runtime_root/pwsh/pwsh" -NoLogo -NoProfile -NonInteractive \
        -Command '$PSVersionTable.PSVersion.ToString()'
    printf '%s\n' "$runtime_root/pwsh" >> "$GITHUB_PATH"
fi

# Official PyPI wheel; no dependency resolution or global Python installation.
download \
    'https://files.pythonhosted.org/packages/bd/48/bcb1e4d329c3d522bc29b066b0b6ee86938ec392376a29c36fac0ad1c586/xonsh-0.20.0-py313-none-any.whl' \
    "$runtime_root/xonsh-0.20.0-py313-none-any.whl" \
    'c83daaf6eb2960180fc5a507459dbdf6c0d6d63e1733c43f4e43db77255c7278'
python_executable=$(uv run --locked python -c 'import sys; print(sys.executable)')
uv venv --python "$python_executable" "$runtime_root/xonsh"
uv pip install --python "$runtime_root/xonsh/bin/python" --no-deps --no-index \
    "$runtime_root/xonsh-0.20.0-py313-none-any.whl"
"$runtime_root/xonsh/bin/xonsh" --no-rc --version
printf '%s\n' "$runtime_root/xonsh/bin" >> "$GITHUB_PATH"
