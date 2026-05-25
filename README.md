> [!NOTE]
> This project was built with [Claude Code](https://claude.ai/code) (Anthropic).

# trilium-import

CLI tool to import local files (`.md`, `.html`, `.txt`) into a self-hosted
[Trilium Notes](https://github.com/zadam/trilium) instance via its ETAPI.

Markdown files are converted to HTML so they render natively inside Trilium
(Trilium has no `markdown` note type — text notes store HTML).

## Requirements

- Python 3.10+
- A Trilium instance with an **ETAPI token** (Settings → ETAPI)
- Network access from the machine running the script to the Trilium server

## Install

```bash
git clone https://forgejo.internal/Felxs/trilium-import.git
cd trilium-import

python -m venv .venv
.venv/bin/pip install requests python-dotenv markdown
```

The script's shebang points at `.venv/bin/python`, so once installed you can
run it directly:

```bash
./trilium_import.py --help
```

Or symlink it to `~/.local/bin/trilium-import` for shell-wide use.

## Setup

Run the interactive setup once:

```bash
./trilium_import.py --setup
```

This writes `~/.trilium-import.env` containing:

```
TRILIUM_URL=https://triliumnotes.internal
TRILIUM_TOKEN=your_etapi_token_here
TRILIUM_DEFAULT_PARENT=root
```

You can also create the file by hand.

### Optional: TLS / internal CA

If your Trilium server uses a certificate signed by an internal CA, the
script resolves the trust store in this order:

1. `TRILIUM_CA_BUNDLE` (path to a PEM bundle) — set in the env file
2. `/etc/ssl/certs/ca-certificates.crt` (system bundle on most Linux distros)
3. Python's bundled `certifi` bundle

Example:

```
TRILIUM_CA_BUNDLE=/etc/ssl/private/my-internal-ca.pem
```

## Usage

```bash
trilium-import <file> [--parent PATH] [--title TITLE] [--dry-run]
```

### Basic

Import a markdown file under the default parent:

```bash
trilium-import notes.md
```

### Rename on import

The note title defaults to the filename without extension. Override it:

```bash
trilium-import notes.md --title "Project Kickoff Notes"
```

### Choose a parent note

`--parent` accepts several forms:

| Form                                    | Behavior                                       |
| --------------------------------------- | ---------------------------------------------- |
| `root`                                  | Top-level                                      |
| `abcDEF123456`                          | Direct note ID (≥10 alphanumeric chars)        |
| `"Import Nodes"`                        | Direct child of root with that title           |
| `"Import Nodes/Project A/Subfolder"`    | Walk the path; each segment is a direct child  |

Examples:

```bash
trilium-import doc.md --parent "Import Nodes"
trilium-import doc.md --parent "Import Nodes/Project A/Subfolder"
trilium-import doc.md --parent abcDEF123456
```

If a segment matches multiple direct children, the first is used and a
warning is printed.

### Dry run

Preview what would be sent without creating anything:

```bash
trilium-import doc.md --parent "Import Nodes" --dry-run
```

## Supported file types

| Extension       | Stored as           | Notes                                  |
| --------------- | ------------------- | -------------------------------------- |
| `.md`           | `text` (HTML)       | Converted via `python-markdown`        |
| `.html`         | `text` (HTML)       | Passed through unchanged               |
| `.txt`, other   | `text` (HTML)       | Escaped and wrapped in `<pre>` block   |

## Troubleshooting

- **`Cannot reach Trilium at <url>`** — DNS or network unreachable. Verify
  with `curl -v <url>`.
- **`TLS verification failed`** — internal/self-signed CA not trusted. Set
  `TRILIUM_CA_BUNDLE` (see TLS section above).
- **`401 Unauthorized`** — token wrong or revoked. Regenerate in Trilium
  Settings → ETAPI.
- **`No child note '<segment>' under '<parent>'`** — the title doesn't exist
  as a direct child of the previous segment. Check spelling and hierarchy.
