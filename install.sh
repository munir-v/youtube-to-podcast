#!/bin/zsh
# Copies the app to ~/Library/Application Support (macOS blocks launchd jobs from reading ~/Documents)
# and installs the launchd job that runs it every 10 minutes. Re-run after changing code or config.toml.
# `./install.sh --uninstall` removes the job but keeps the deployed copy and its state.
set -e
LABEL="local.youtube-to-podcast"
PROJECT="${0:A:h}"
APP="$HOME/Library/Application Support/youtube-to-podcast"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
if [[ "$1" == "--uninstall" ]]; then
  rm -f "$PLIST"
  echo "Removed $LABEL (state is still in $APP)"
  exit 0
fi

mkdir -p "$APP"
cp "$PROJECT"/{ytpod.py,run.sh,cover.png,config.toml,requirements.txt} "$APP/"
[[ -d "$APP/.venv" ]] || python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install -q --disable-pip-version-check -r "$APP/requirements.txt"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$APP/run.sh</string></array>
  <key>StartInterval</key><integer>600</integer>
  <key>RunAtLoad</key><true/>
  <key>Nice</key><integer>10</integer>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/youtube-to-podcast.launchd.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/youtube-to-podcast.launchd.log</string>
</dict>
</plist>
EOF
launchctl bootstrap "$DOMAIN" "$PLIST"
echo "Installed $LABEL from $APP: runs every 10 minutes. Log: ~/Library/Logs/youtube-to-podcast.log"
