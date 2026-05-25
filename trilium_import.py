#!/usr/bin/env python3
"""
trilium-import: CLI tool to import files into a self-hosted Trilium Notes instance.

Usage:
    python trilium_import.py ./my-doc.md
    python trilium_import.py ./my-doc.md --parent "Documentation" --title "My Doc"
    python trilium_import.py ./my-doc.md --parent abc123def456

Config (~/.trilium-import.env):
    TRILIUM_URL=http://192.168.1.x:8080
    TRILIUM_TOKEN=your_api_token
    TRILIUM_DEFAULT_PARENT=root
"""

import argparse
import os
import sys
from pathlib import Path

try:
    import requests
except ImportError:
    print("Error: 'requests' not installed. Run: pip install requests")
    sys.exit(1)

try:
    from dotenv import load_dotenv
except ImportError:
    print("Error: 'python-dotenv' not installed. Run: pip install python-dotenv")
    sys.exit(1)

try:
    import markdown as _markdown
except ImportError:
    print("Error: 'markdown' not installed. Run: pip install markdown")
    sys.exit(1)

import html as _html


# ── Config loading ────────────────────────────────────────────────────────────

def load_config() -> dict:
    """Load config from ~/.trilium-import.env, then fall back to env vars."""
    config_path = Path.home() / ".trilium-import.env"
    if config_path.exists():
        load_dotenv(config_path)
    else:
        load_dotenv()  # fall back to .env in cwd

    url   = os.getenv("TRILIUM_URL", "").rstrip("/")
    token = os.getenv("TRILIUM_TOKEN", "")
    default_parent = os.getenv("TRILIUM_DEFAULT_PARENT", "root")

    if not url or not token:
        print(
            "Error: TRILIUM_URL and TRILIUM_TOKEN must be set.\n"
            f"Create {config_path} with:\n\n"
            "  TRILIUM_URL=http://192.168.1.x:8080\n"
            "  TRILIUM_TOKEN=your_api_token\n"
            "  TRILIUM_DEFAULT_PARENT=root\n"
        )
        sys.exit(1)

    # Resolve TLS verify setting:
    # 1. Explicit TRILIUM_CA_BUNDLE wins.
    # 2. Otherwise, use system CA bundle if present (covers internal CAs trusted by the OS).
    # 3. Otherwise, fall back to certifi default (True).
    ca_bundle = os.getenv("TRILIUM_CA_BUNDLE", "").strip()
    if ca_bundle:
        verify: str | bool = ca_bundle
    elif Path("/etc/ssl/certs/ca-certificates.crt").exists():
        verify = "/etc/ssl/certs/ca-certificates.crt"
    else:
        verify = True

    return {
        "url": url,
        "token": token,
        "default_parent": default_parent,
        "verify": verify,
    }


# ── Trilium API helpers ───────────────────────────────────────────────────────

def make_headers(token: str) -> dict:
    # Trilium ETAPI expects the raw token as the Authorization header value
    # (not Basic, not Bearer).
    return {"Authorization": token}


def _looks_like_note_id(s: str) -> bool:
    """Trilium note IDs are alphanumeric and typically ≥10 chars with no spaces."""
    return len(s) >= 10 and s.isalnum()


def search_children(
    base_url: str,
    token: str,
    parent_id: str,
    title: str,
    verify: str | bool = True,
) -> list[dict]:
    """
    Find notes whose title matches `title` directly under `parent_id`.
    Prefers exact title match; falls back to fuzzy if no exact hits.
    """
    try:
        resp = requests.get(
            f"{base_url}/etapi/notes",
            headers=make_headers(token),
            params={
                "search": title,
                "ancestorNoteId": parent_id,
                "ancestorDepth": "eq1",
            },
            timeout=10,
            verify=verify,
        )
        resp.raise_for_status()
    except requests.exceptions.SSLError as e:
        print(
            f"Error: TLS verification failed for {base_url}.\n"
            f"  {e}\n"
            "  Set TRILIUM_CA_BUNDLE in ~/.trilium-import.env to a CA bundle that trusts the server cert."
        )
        sys.exit(1)
    except requests.exceptions.ConnectionError:
        print(f"Error: Cannot reach Trilium at {base_url}. Is it running and on the same network?")
        sys.exit(1)
    except requests.exceptions.HTTPError as e:
        print(f"Error searching for note: {e}")
        sys.exit(1)

    results = resp.json().get("results", [])
    target = title.casefold()
    exact = [r for r in results if r.get("title", "").casefold() == target]
    return exact or results


def resolve_parent(base_url: str, token: str, parent_arg: str, verify: str | bool = True) -> str:
    """
    Resolve --parent to a note ID.

    Accepts:
      - "root"                          → root note
      - "abcdef123456"                  → direct note ID
      - "Import Nodes"                  → search title directly under root
      - "Import Nodes/Project A/Sub"    → walk path, each segment is a direct
                                          child of the previous one
    Any segment that looks like a note ID is used as-is (no search).
    """
    if parent_arg == "root":
        return "root"

    segments = [s.strip() for s in parent_arg.split("/") if s.strip()]
    if not segments:
        return "root"

    current = "root"
    for seg in segments:
        if _looks_like_note_id(seg):
            print(f"  Using '{seg}' as direct note ID.")
            current = seg
            continue

        matches = search_children(base_url, token, current, seg, verify=verify)
        if not matches:
            parent_label = "root" if current == "root" else current
            print(f"Error: No child note '{seg}' under '{parent_label}'.")
            sys.exit(1)

        if len(matches) > 1:
            titles = ", ".join(repr(m.get("title", "?")) for m in matches[:5])
            print(f"  Warning: multiple matches for '{seg}' ({titles}). Using first.")

        match = matches[0]
        print(f"  Resolved '{seg}' → '{match.get('title', '?')}' (ID: {match.get('noteId')})")
        current = match.get("noteId")

    return current


def prepare_content(file_path: Path, raw: str) -> tuple[str, str]:
    """
    Convert file content into a (content, note_type) pair suitable for Trilium's
    ETAPI create-note endpoint. Trilium has no native "markdown" or "html" type:
    text notes store HTML. So we normalize everything to type="text".
    """
    ext = file_path.suffix.lower()
    if ext == ".md":
        html_body = _markdown.markdown(
            raw,
            extensions=["fenced_code", "tables", "sane_lists", "nl2br"],
        )
        return html_body, "text"
    if ext == ".html":
        return raw, "text"
    # .txt and unknown: escape and wrap in <pre> so whitespace is preserved.
    return f"<pre>{_html.escape(raw)}</pre>", "text"


def create_note(
    base_url: str,
    token: str,
    parent_id: str,
    title: str,
    content: str,
    note_type: str,
    verify: str | bool = True,
) -> dict:
    """Create a note via the Trilium API and return the response JSON."""
    payload = {
        "parentNoteId": parent_id,
        "title": title,
        "content": content,
        "type": note_type,
    }
    try:
        resp = requests.post(
            f"{base_url}/etapi/create-note",
            headers={**make_headers(token), "Content-Type": "application/json"},
            json=payload,
            timeout=15,
            verify=verify,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.SSLError as e:
        print(
            f"Error: TLS verification failed for {base_url}.\n"
            f"  {e}\n"
            "  Set TRILIUM_CA_BUNDLE in ~/.trilium-import.env to a CA bundle that trusts the server cert."
        )
        sys.exit(1)
    except requests.exceptions.ConnectionError:
        print(f"Error: Cannot reach Trilium at {base_url}.")
        sys.exit(1)
    except requests.exceptions.HTTPError as e:
        print(f"Error creating note: {e}\nResponse: {resp.text}")
        sys.exit(1)


# ── Setup helper ──────────────────────────────────────────────────────────────

def run_setup():
    """Interactive first-time setup: write ~/.trilium-import.env."""
    config_path = Path.home() / ".trilium-import.env"
    print("Trilium Import — First-time setup")
    print("=" * 40)
    url   = input("Trilium URL (e.g. http://192.168.1.10:8080): ").strip().rstrip("/")
    token = input("API token (Settings → API tokens in Trilium): ").strip()
    parent = input("Default parent note ID or 'root' [root]: ").strip() or "root"

    config_path.write_text(
        f"TRILIUM_URL={url}\n"
        f"TRILIUM_TOKEN={token}\n"
        f"TRILIUM_DEFAULT_PARENT={parent}\n"
    )
    print(f"\nConfig saved to {config_path}")
    print("You can now run: python trilium_import.py <file>")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="trilium-import",
        description="Import a file into your self-hosted Trilium Notes instance.",
    )
    parser.add_argument(
        "file",
        nargs="?",
        help="Path to the file to import (.md, .txt, .html)",
    )
    parser.add_argument(
        "--parent", "-p",
        default=None,
        help=(
            "Parent note: 'root', a note ID, a title, or a slash-separated path "
            "(e.g. 'Import Nodes/Project A/Subfolder'). "
            "Overrides TRILIUM_DEFAULT_PARENT."
        ),
    )
    parser.add_argument(
        "--title", "-t",
        default=None,
        help="Note title (defaults to filename without extension)",
    )
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Run interactive setup to create ~/.trilium-import.env",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be sent without actually creating the note",
    )

    args = parser.parse_args()

    # Setup mode
    if args.setup:
        run_setup()
        return

    if not args.file:
        parser.print_help()
        sys.exit(1)

    # Load config
    config = load_config()

    # Resolve file
    file_path = Path(args.file).expanduser().resolve()
    if not file_path.exists():
        print(f"Error: File not found: {file_path}")
        sys.exit(1)
    if not file_path.is_file():
        print(f"Error: Not a file: {file_path}")
        sys.exit(1)

    # Read content
    try:
        content = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = file_path.read_text(encoding="latin-1")

    # Resolve title
    title = args.title or file_path.stem

    # Convert content to Trilium-compatible HTML + note type
    content, note_type = prepare_content(file_path, content)

    # Resolve parent
    parent_arg = args.parent or config["default_parent"]
    parent_id  = resolve_parent(config["url"], config["token"], parent_arg, verify=config["verify"])

    # Summary
    print(f"\n  File   : {file_path}")
    print(f"  Title  : {title}")
    print(f"  Type   : {note_type}")
    print(f"  Parent : {parent_id}")
    print(f"  Size   : {len(content)} chars\n")

    if args.dry_run:
        print("Dry run — no note created.")
        return

    # Create the note
    result = create_note(
        base_url=config["url"],
        token=config["token"],
        parent_id=parent_id,
        title=title,
        content=content,
        note_type=note_type,
        verify=config["verify"],
    )

    note_id = result.get("note", {}).get("noteId") or result.get("noteId", "?")
    print(f"✓ Note created successfully! ID: {note_id}")
    print(f"  Open: {config['url']}/#root/{note_id}")


if __name__ == "__main__":
    main()
