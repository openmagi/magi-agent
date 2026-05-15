#!/bin/sh
# Notion KB sync — builds metadata index for intelligent querying
# Usage:
#   notion-kb-sync.sh full     — Full sync (all pages + databases)
#   notion-kb-sync.sh incremental — Only changed since last sync
#   notion-kb-sync.sh status   — Show sync status
#
# Creates: workspace/knowledge/notion-index/
#   _catalog.json, page-{id}.md, gdrive-{id}.md

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORKSPACE="$HOME/workspace"
INDEX_DIR="$WORKSPACE/knowledge/notion-index"
CONFIG_FILE="$WORKSPACE/config/notion-kb.json"
MODE="${1:-incremental}"

# ── Helpers ──

fetch_notion() {
  "$SCRIPT_DIR/integration.sh" "notion/$1" 2>/dev/null
}

fetch_google() {
  "$SCRIPT_DIR/integration.sh" "google/$1" 2>/dev/null
}

log() {
  echo "[notion-kb] $1" >&2
}

# ── Init ──

mkdir -p "$INDEX_DIR" "$WORKSPACE/config"

if [ "$MODE" = "status" ]; then
  if [ -f "$CONFIG_FILE" ]; then
    cat "$CONFIG_FILE"
  else
    echo '{"enabled":false,"last_sync":null}'
  fi
  exit 0
fi

# Create config if missing
if [ ! -f "$CONFIG_FILE" ]; then
  echo '{"enabled":true,"scope":"all","last_sync":null}' > "$CONFIG_FILE"
fi

LAST_SYNC=""
if [ "$MODE" = "incremental" ] && [ -f "$CONFIG_FILE" ]; then
  LAST_SYNC=$(python3 -c "
import json, sys
try:
  c = json.load(open('$CONFIG_FILE'))
  print(c.get('last_sync') or '')
except: pass
" 2>/dev/null)
fi

log "Starting $MODE sync (last_sync=$LAST_SYNC)"

# ── Fetch pages (with pagination) ──

ALL_PAGES="[]"
CURSOR=""
PAGE_NUM=0

while true; do
  if [ -n "$CURSOR" ]; then
    RESULT=$(fetch_notion "pages?start_cursor=$CURSOR")
  else
    RESULT=$(fetch_notion "pages")
  fi

  if echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if 'pages' in d else 1)" 2>/dev/null; then
    BATCH=$(echo "$RESULT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(json.dumps(d.get('pages', [])))
")
    ALL_PAGES=$(python3 -c "
import json
a = json.loads('$ALL_PAGES' if '$ALL_PAGES' != '[]' else '[]')
b = json.loads('''$BATCH''')
print(json.dumps(a + b))
" 2>/dev/null || echo "$ALL_PAGES")

    HAS_MORE=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('has_more',False))" 2>/dev/null)
    if [ "$HAS_MORE" = "True" ]; then
      CURSOR=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('next_cursor',''))" 2>/dev/null)
      PAGE_NUM=$((PAGE_NUM + 1))
      log "Fetched page batch $PAGE_NUM..."
      sleep 1
    else
      break
    fi
  else
    log "Error fetching pages: $RESULT"
    break
  fi
done

# ── Fetch databases ──

DB_RESULT=$(fetch_notion "databases")
ALL_DBS=$(echo "$DB_RESULT" | python3 -c "
import sys, json
try:
  d = json.load(sys.stdin)
  print(json.dumps(d.get('databases', [])))
except:
  print('[]')
" 2>/dev/null || echo "[]")

TOTAL_PAGES=$(echo "$ALL_PAGES" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")
TOTAL_DBS=$(echo "$ALL_DBS" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")
log "Found $TOTAL_PAGES pages, $TOTAL_DBS databases"

# ── Build index ──

python3 << 'PYEOF'
import json, sys, os, time

INDEX_DIR = os.environ.get("INDEX_DIR", "")
LAST_SYNC = os.environ.get("LAST_SYNC", "")
MODE = os.environ.get("MODE", "full")
SCRIPT_DIR = os.environ.get("SCRIPT_DIR", "")

# Read pages and databases from stdin-like approach
pages_str = os.environ.get("ALL_PAGES", "[]")
dbs_str = os.environ.get("ALL_DBS", "[]")

try:
    pages = json.loads(pages_str)
except:
    pages = []
try:
    databases = json.loads(dbs_str)
except:
    databases = []

catalog = {
    "last_sync": None,
    "pages": [],
    "databases": []
}

# Load existing catalog for incremental
existing_catalog = {}
catalog_path = os.path.join(INDEX_DIR, "_catalog.json")
if MODE == "incremental" and os.path.exists(catalog_path):
    try:
        with open(catalog_path) as f:
            existing_catalog = json.load(f)
    except:
        pass

synced = 0
skipped = 0
errors = 0

for p in pages:
    pid = p.get("id", "").replace("-", "")
    title = p.get("title", "(Untitled)")
    last_edited = p.get("last_edited", "")
    parent_id = p.get("parent_id")
    url = p.get("url", "")

    # Incremental: skip unchanged
    if MODE == "incremental" and LAST_SYNC and last_edited:
        if last_edited <= LAST_SYNC:
            # Keep existing catalog entry
            for ep in existing_catalog.get("pages", []):
                if ep.get("id") == pid:
                    catalog["pages"].append(ep)
                    break
            skipped += 1
            continue

    # Fetch page content for summary
    try:
        import subprocess
        result = subprocess.run(
            [os.path.join(SCRIPT_DIR, "integration.sh"), f"notion/page/{p['id']}"],
            capture_output=True, text=True, timeout=30
        )
        page_data = json.loads(result.stdout) if result.stdout else {}
    except Exception as e:
        print(f"[notion-kb] Error fetching page {pid}: {e}", file=sys.stderr)
        errors += 1
        catalog["pages"].append({
            "id": pid, "title": title, "parent_id": parent_id,
            "last_edited": last_edited, "has_content_file": False,
            "linked_gdrive": []
        })
        continue

    content = page_data.get("content", [])

    # Extract structure
    headings = []
    gdrive_links = []
    summary_parts = []
    for block in content[:50]:  # Limit to first 50 blocks
        btype = block.get("type", "")
        text = block.get("text", "")
        burl = block.get("url", "")

        if btype.startswith("heading"):
            headings.append(text)
        elif btype == "paragraph" and text and len(summary_parts) < 3:
            summary_parts.append(text[:200])

        # Detect Google Drive links
        if burl and ("docs.google.com/spreadsheets" in burl or "sheets.google.com" in burl):
            # Extract spreadsheet ID from URL
            parts = burl.split("/d/")
            if len(parts) > 1:
                sheet_id = parts[1].split("/")[0].split("?")[0]
                gdrive_links.append(sheet_id)

    summary = " ".join(summary_parts)[:500] if summary_parts else ""

    # Write page-{id}.md
    md_lines = [f"# {title}", ""]
    md_lines.append(f"- **Notion ID**: {pid}")
    if url:
        md_lines.append(f"- **URL**: {url}")
    md_lines.append(f"- **Last edited**: {last_edited}")
    if parent_id:
        md_lines.append(f"- **Parent**: {parent_id}")
    md_lines.append("")

    if summary:
        md_lines.append("## Summary")
        md_lines.append(summary)
        md_lines.append("")

    if headings:
        md_lines.append("## Sections")
        for h in headings:
            md_lines.append(f"- {h}")
        md_lines.append("")

    if gdrive_links:
        md_lines.append("## Linked Google Sheets")
        for gid in gdrive_links:
            md_lines.append(f"- Spreadsheet: {gid}")
        md_lines.append("")

    page_file = os.path.join(INDEX_DIR, f"page-{pid}.md")
    with open(page_file, "w") as f:
        f.write("\n".join(md_lines))

    catalog["pages"].append({
        "id": pid,
        "title": title,
        "parent_id": parent_id,
        "last_edited": last_edited,
        "has_content_file": True,
        "linked_gdrive": gdrive_links,
        "sections": headings[:10],
    })
    synced += 1

    # Rate limit: ~2 requests/sec
    time.sleep(0.5)

# Process databases
for db in databases:
    dbid = db.get("id", "").replace("-", "")
    title = db.get("title", "(Untitled)")
    last_edited = db.get("last_edited", "")
    url = db.get("url", "")

    # Incremental: skip unchanged
    if MODE == "incremental" and LAST_SYNC and last_edited:
        if last_edited <= LAST_SYNC:
            for edb in existing_catalog.get("databases", []):
                if edb.get("id") == dbid:
                    catalog["databases"].append(edb)
                    break
            continue

    # Fetch database rows to get schema
    try:
        result = subprocess.run(
            [os.path.join(SCRIPT_DIR, "integration.sh"), f"notion/database/{db['id']}"],
            capture_output=True, text=True, timeout=30
        )
        db_data = json.loads(result.stdout) if result.stdout else {}
    except Exception as e:
        print(f"[notion-kb] Error fetching database {dbid}: {e}", file=sys.stderr)
        errors += 1
        catalog["databases"].append({
            "id": dbid, "title": title, "columns": [], "row_count": 0
        })
        continue

    rows = db_data.get("rows", [])
    columns = []
    if rows:
        columns = list(rows[0].get("properties", {}).keys())

    catalog["databases"].append({
        "id": dbid,
        "title": title,
        "url": url,
        "last_edited": last_edited,
        "columns": columns,
        "row_count": len(rows),
    })
    synced += 1
    time.sleep(0.5)

# Fetch Google Sheets metadata for linked spreadsheets
all_gdrive_ids = set()
for p in catalog["pages"]:
    for gid in p.get("linked_gdrive", []):
        all_gdrive_ids.add(gid)

for sheet_id in all_gdrive_ids:
    try:
        result = subprocess.run(
            [os.path.join(SCRIPT_DIR, "integration.sh"), f"google/sheets-metadata?spreadsheetId={sheet_id}"],
            capture_output=True, text=True, timeout=30
        )
        sheet_data = json.loads(result.stdout) if result.stdout else {}
        if "error" in sheet_data:
            continue

        sheet_title = sheet_data.get("title", "Untitled Sheet")
        sheets = sheet_data.get("sheets", [])

        md_lines = [f"# {sheet_title} (Google Sheet)", ""]
        md_lines.append(f"- **Spreadsheet ID**: {sheet_id}")
        md_lines.append("")
        md_lines.append("## Sheets")

        for s in sheets:
            sname = s.get("title", "Sheet")
            cols = s.get("headers", [])
            rows = s.get("gridProperties", {}).get("rowCount", 0)
            md_lines.append(f"### {sname}")
            if cols:
                md_lines.append(f"- Columns: {' | '.join(str(c) for c in cols)}")
            md_lines.append(f"- Rows: {rows}")
            md_lines.append("")

        gdrive_file = os.path.join(INDEX_DIR, f"gdrive-{sheet_id}.md")
        with open(gdrive_file, "w") as f:
            f.write("\n".join(md_lines))
    except Exception as e:
        print(f"[notion-kb] Error fetching sheet {sheet_id}: {e}", file=sys.stderr)
    time.sleep(0.5)

# Write catalog
now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
catalog["last_sync"] = now

with open(catalog_path, "w") as f:
    json.dump(catalog, f, indent=2, ensure_ascii=False)

# Update config
config_path = os.environ.get("CONFIG_FILE", "")
if config_path and os.path.exists(config_path):
    try:
        with open(config_path) as f:
            config = json.load(f)
        config["last_sync"] = now
        config["enabled"] = True
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
    except:
        pass

print(json.dumps({
    "status": "ok",
    "mode": MODE,
    "pages_synced": synced,
    "pages_skipped": skipped,
    "databases": len(catalog["databases"]),
    "gdrive_files": len(all_gdrive_ids),
    "errors": errors,
    "total_pages": len(catalog["pages"]),
    "last_sync": now,
}))
PYEOF
