# Background pipeline

Run `.venv/bin/python -m distill.scheduled` from the repository to collect Slack
articles through Claude Code's authenticated global `slack` MCP connection,
ingest them, and then execute `distill run`.

The wrapper reads channel IDs, recency, reaction thresholds, and result limits
from `config.yaml`. Only the Slack channel read tool is permitted. Failed or
incomplete Slack reads stop the job before the pipeline starts. A successful
read with no qualifying articles still runs the pipeline. Claude Code must be
installed, signed in, and authenticated to Slack; expired authentication needs
to be renewed interactively.

The local macOS LaunchAgent is `com.ankit.distill.pipeline`, installed under
`~/Library/LaunchAgents/`. It runs every 259200 seconds (three days), and on
loading the job at login. It survives terminal closure. The Mac must be awake
and the user logged in for interval execution; this is not an always-on server.
Launchd prevents overlapping instances of this job.

Logs are in `~/Library/Logs/Distill/pipeline.log` and `pipeline.error.log`.
Check status with `launchctl print gui/$(id -u)/com.ankit.distill.pipeline`.
Stop the loaded job with `launchctl bootout gui/$(id -u)/com.ankit.distill.pipeline`.
To prevent loading at the next login, also rename the LaunchAgent's `.plist`
file to `.plist.disabled`.
