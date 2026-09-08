# Main Runtime Installation

The Control Panel and custom blue-green installer are retired. The only local
entrypoint is `scripts/run_main_services.ps1`, with Run, Start, Stop and StatusJson.
Use the fixed runtime root and existing configuration repository root. Start uses
the hidden VBS launcher. A scheduled task should invoke that launcher at login;
only one root-specific process owner may run. Never point production at a PR.

Installation is pending final review and real takeover verification. The previous
Autostart and Guard tasks are disabled; their exported definitions are audit.
Do not remove installed supervision or terminate business processes before the
new entrypoint is verified. No backup version or rollback task is installed.
