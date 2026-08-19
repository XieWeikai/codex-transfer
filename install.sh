#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
install_root=${CODEX_TRANSFER_INSTALL_ROOT:-${XDG_DATA_HOME:-$HOME/.local/share}/codex-transfer}
bin_dir=${CODEX_TRANSFER_BIN_DIR:-${XDG_BIN_HOME:-$HOME/.local/bin}}
codex_skills_dir=${CODEX_TRANSFER_CODEX_SKILLS_DIR:-$HOME/.agents/skills}
claude_skills_dir=${CODEX_TRANSFER_CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}

if [ -n "${PYTHON_BIN:-}" ]; then
  python_bin=$PYTHON_BIN
else
  python_bin=""
  for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'; then
      python_bin=$candidate
      break
    fi
  done
fi

if [ -z "$python_bin" ]; then
  echo "Codex Transfer requires Python 3.11 or newer." >&2
  exit 1
fi

mkdir -p "$install_root" "$bin_dir"
"$python_bin" -m venv "$install_root/venv"
release_dir="$install_root/releases/$(date +%Y%m%d%H%M%S)"
mkdir -p "$release_dir"
cp -R "$project_dir/src/codex_transfer" "$release_dir/"
mkdir -p "$release_dir/skills/codex" "$release_dir/skills/claude"
cp -R "$project_dir/.agents/skills/codex-transfer" "$release_dir/skills/codex/"
cp -R "$project_dir/.claude/skills/codex-transfer" "$release_dir/skills/claude/"
ln -sfn "$release_dir" "$install_root/current"

launcher="$install_root/codex-transfer"
launcher_tmp="$install_root/.codex-transfer.tmp"
printf '%s\n' \
  '#!/bin/sh' \
  "export PYTHONPATH='$install_root/current'" \
  "exec '$install_root/venv/bin/python' -m codex_transfer.cli \"\$@\"" \
  > "$launcher_tmp"
chmod +x "$launcher_tmp"
mv "$launcher_tmp" "$launcher"

for command_name in codex-transfer ct; do
  destination="$bin_dir/$command_name"
  if [ -e "$destination" ] && [ ! -L "$destination" ]; then
    echo "Refusing to replace existing file: $destination" >&2
    exit 1
  fi
  ln -sfn "$launcher" "$destination"
done

remove_legacy_link() {
  destination=$1
  [ -L "$destination" ] || return 0
  target=$(readlink "$destination")
  case "$target" in
    *"/codex-relay/"*|*"/codex-relay")
      rm -- "$destination"
      echo "Removed legacy link: $destination"
      ;;
  esac
}

for command_name in codex-relay csm codex-session-manager; do
  remove_legacy_link "$bin_dir/$command_name"
done

install_skill() {
  source_dir=$1
  skills_dir=$2
  product=$3
  destination="$skills_dir/codex-transfer"
  mkdir -p "$skills_dir"
  if [ -e "$destination" ] && [ ! -L "$destination" ]; then
    echo "Skipping $product skill; refusing to replace existing path: $destination" >&2
    return
  fi
  ln -sfn "$source_dir" "$destination"
  echo "$product skill: $destination"
}

install_skill "$install_root/current/skills/codex/codex-transfer" "$codex_skills_dir" "Codex"
install_skill "$install_root/current/skills/claude/codex-transfer" "$claude_skills_dir" "Claude Code"
remove_legacy_link "$codex_skills_dir/codex-relay"
remove_legacy_link "$claude_skills_dir/codex-relay"

echo "Codex Transfer installed."
echo "Run: codex-transfer (or ct)"
case ":$PATH:" in
  *":$bin_dir:"*) ;;
  *) echo "Add $bin_dir to PATH before using the command from a new shell." ;;
esac
