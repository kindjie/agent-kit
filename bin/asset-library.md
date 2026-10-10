# asset-library

Requires a POSIX system (macOS or Linux): it relies on file ownership, mode
bits and `O_NOFOLLOW`.

A foreground launcher for an explicitly configured owning backend. It accepts
`status`, `doctor`, `fetch --revision FULL_COMMIT --paths-file SELECTION
--into OUTPUT`, `publish [--flush]` and `tunnel run -- COMMAND [ARG ...]`.
A revision must be a full lowercase commit OID.

The default launcher configuration is
`$XDG_CONFIG_HOME/asset-library/client.json`, or
`~/.config/asset-library/client.json` when that variable is unset. Override it
with `--client-config FILE` before the subcommand. Configuration must be an
owned regular file readable only by its owner (mode 0600 or 0400), with
exactly three fields:

```json
{
  "backend": ["/absolute/python", "-B", "-m", "owning_backend.client"],
  "directory": "/absolute/backend-checkout",
  "config": "/absolute/private-backend-config.json"
}
```

These paths are illustrative. The executable and paths must be absolute and
the backend directory must exist. Relative fetch arguments are converted
against the caller's directory before entering the backend directory. The
launcher executes literal arguments without a shell and preserves the
backend's exit status and foreground signal behavior.

The backend owns credential storage, remote trust, content verification,
protected output roots, publication admission and backup status. This launcher
adds no alternative asset writer, importer, service or recovery policy. Keep
private repository locations and credentials in local configuration.

The tunnel backend owns its configured listener and foreground child. The
launcher forwards the original caller directory and literal command arguments;
it does not discover endpoints, terminate listeners or handle credentials.
