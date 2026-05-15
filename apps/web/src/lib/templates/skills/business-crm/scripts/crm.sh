#!/bin/bash
# Business CRM — SQLite-backed structured data for contacts, companies, deals, activities, tasks
# Usage: bash scripts/crm.sh <command> [args...]
set -euo pipefail

DB="${CRM_DB:-$HOME/.openclaw/workspace/knowledge/crm.db}"

# Ensure DB directory exists
mkdir -p "$(dirname "$DB")"

# ── Init ──────────────────────────────────────────────────────────────────────
init_db() {
  sqlite3 "$DB" <<'SQL'
CREATE TABLE IF NOT EXISTS companies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  industry TEXT,
  website TEXT,
  notes TEXT,
  created_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS contacts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  company_id INTEGER REFERENCES companies(id),
  name TEXT NOT NULL,
  email TEXT,
  phone TEXT,
  role TEXT,
  tags TEXT,
  notes TEXT,
  last_contact TEXT,
  created_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS deals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  contact_id INTEGER REFERENCES contacts(id),
  company_id INTEGER REFERENCES companies(id),
  title TEXT NOT NULL,
  amount REAL,
  currency TEXT DEFAULT 'KRW',
  stage TEXT DEFAULT 'lead',
  expected_close TEXT,
  notes TEXT,
  created_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS activities (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  contact_id INTEGER REFERENCES contacts(id),
  deal_id INTEGER REFERENCES deals(id),
  type TEXT NOT NULL,
  summary TEXT NOT NULL,
  occurred_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  contact_id INTEGER REFERENCES contacts(id),
  deal_id INTEGER REFERENCES deals(id),
  title TEXT NOT NULL,
  due_date TEXT,
  priority TEXT DEFAULT 'medium',
  status TEXT DEFAULT 'todo',
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_contacts_company ON contacts(company_id);
CREATE INDEX IF NOT EXISTS idx_deals_stage ON deals(stage);
CREATE INDEX IF NOT EXISTS idx_deals_contact ON deals(contact_id);
CREATE INDEX IF NOT EXISTS idx_activities_contact ON activities(contact_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(due_date);
PRAGMA journal_mode=WAL;
SQL
  echo "CRM database initialized at $DB"
}

# Auto-init on first use
[ -f "$DB" ] || init_db

# ── Helpers ───────────────────────────────────────────────────────────────────
sql() {
  sqlite3 -header -column "$DB" "$@"
}

sql_json() {
  sqlite3 -json "$DB" "$@"
}

escape() {
  # Escape single quotes for SQL string literals
  printf '%s' "$1" | sed "s/'/''/g"
}

# ── Contacts ──────────────────────────────────────────────────────────────────
add_contact() {
  local name="${1:?name required}"
  local email="${2:-}"
  local phone="${3:-}"
  local company="${4:-}"
  local role="${5:-}"
  local tags="${6:-}"

  local company_id="NULL"
  if [ -n "$company" ]; then
    # Find or create company
    company_id=$(sqlite3 "$DB" "SELECT id FROM companies WHERE name='$(escape "$company")' LIMIT 1;")
    if [ -z "$company_id" ]; then
      sqlite3 "$DB" "INSERT INTO companies (name) VALUES ('$(escape "$company")');"
      company_id=$(sqlite3 "$DB" "SELECT last_insert_rowid();")
      echo "Auto-created company: $company (id: $company_id)"
    fi
  fi

  sqlite3 "$DB" "INSERT INTO contacts (name, email, phone, company_id, role, tags, last_contact)
    VALUES ('$(escape "$name")', '$(escape "$email")', '$(escape "$phone")', $company_id, '$(escape "$role")', '$(escape "$tags")', date('now'));"
  local id=$(sqlite3 "$DB" "SELECT last_insert_rowid();")
  echo "Contact added: $name (id: $id)"
  sql "SELECT c.*, co.name AS company FROM contacts c LEFT JOIN companies co ON c.company_id=co.id WHERE c.id=$id;"
}

find_contacts() {
  local query="${1:?search query required}"
  local q="$(escape "$query")"
  sql "SELECT c.id, c.name, c.email, c.phone, co.name AS company, c.role, c.tags, c.last_contact
    FROM contacts c LEFT JOIN companies co ON c.company_id=co.id
    WHERE c.name LIKE '%$q%' OR c.email LIKE '%$q%' OR co.name LIKE '%$q%'
      OR c.role LIKE '%$q%' OR c.tags LIKE '%$q%'
    ORDER BY c.updated_at DESC LIMIT 20;"
}

update_contact() {
  local id="${1:?contact id required}"
  shift
  local sets=""
  while [ $# -ge 2 ]; do
    local field="$1" value="$2"
    case "$field" in
      name|email|phone|role|tags|notes|last_contact)
        sets="${sets}${sets:+, }$field='$(escape "$value")'"
        ;;
      company_id)
        sets="${sets}${sets:+, }company_id=$value"
        ;;
      *) echo "Unknown field: $field"; return 1 ;;
    esac
    shift 2
  done
  [ -n "$sets" ] || { echo "No fields to update"; return 1; }
  sqlite3 "$DB" "UPDATE contacts SET $sets, updated_at=datetime('now') WHERE id=$id;"
  echo "Contact $id updated"
  sql "SELECT c.*, co.name AS company FROM contacts c LEFT JOIN companies co ON c.company_id=co.id WHERE c.id=$id;"
}

list_contacts() {
  local limit="${1:-20}"
  local tag="${2:-}"
  local where=""
  [ -n "$tag" ] && where="WHERE c.tags LIKE '%$(escape "$tag")%'"
  sql "SELECT c.id, c.name, c.email, c.phone, co.name AS company, c.role, c.tags, c.last_contact
    FROM contacts c LEFT JOIN companies co ON c.company_id=co.id $where
    ORDER BY c.updated_at DESC LIMIT $limit;"
}

# ── Companies ─────────────────────────────────────────────────────────────────
add_company() {
  local name="${1:?name required}"
  local industry="${2:-}"
  local website="${3:-}"
  sqlite3 "$DB" "INSERT INTO companies (name, industry, website)
    VALUES ('$(escape "$name")', '$(escape "$industry")', '$(escape "$website")');"
  local id=$(sqlite3 "$DB" "SELECT last_insert_rowid();")
  echo "Company added: $name (id: $id)"
  sql "SELECT * FROM companies WHERE id=$id;"
}

find_companies() {
  local query="${1:?search query required}"
  local q="$(escape "$query")"
  sql "SELECT * FROM companies WHERE name LIKE '%$q%' OR industry LIKE '%$q%' ORDER BY updated_at DESC LIMIT 20;"
}

update_company() {
  local id="${1:?company id required}"
  shift
  local sets=""
  while [ $# -ge 2 ]; do
    local field="$1" value="$2"
    case "$field" in
      name|industry|website|notes)
        sets="${sets}${sets:+, }$field='$(escape "$value")'"
        ;;
      *) echo "Unknown field: $field"; return 1 ;;
    esac
    shift 2
  done
  [ -n "$sets" ] || { echo "No fields to update"; return 1; }
  sqlite3 "$DB" "UPDATE companies SET $sets, updated_at=datetime('now') WHERE id=$id;"
  echo "Company $id updated"
  sql "SELECT * FROM companies WHERE id=$id;"
}

# ── Deals ─────────────────────────────────────────────────────────────────────
add_deal() {
  local title="${1:?title required}"
  local contact_id="${2:-NULL}"
  local company_id="${3:-NULL}"
  local amount="${4:-NULL}"
  local stage="${5:-lead}"
  local expected_close="${6:-}"
  local close_val="NULL"
  [ -n "$expected_close" ] && close_val="'$(escape "$expected_close")'"
  sqlite3 "$DB" "INSERT INTO deals (title, contact_id, company_id, amount, stage, expected_close)
    VALUES ('$(escape "$title")', $contact_id, $company_id, $amount, '$(escape "$stage")', $close_val);"
  local id=$(sqlite3 "$DB" "SELECT last_insert_rowid();")
  echo "Deal added: $title (id: $id, stage: $stage)"
  sql "SELECT d.*, c.name AS contact, co.name AS company
    FROM deals d LEFT JOIN contacts c ON d.contact_id=c.id LEFT JOIN companies co ON d.company_id=co.id
    WHERE d.id=$id;"
}

move_deal() {
  local id="${1:?deal id required}"
  local stage="${2:?stage required}"
  case "$stage" in
    lead|qualified|proposal|negotiation|won|lost) ;;
    *) echo "Invalid stage: $stage (valid: lead, qualified, proposal, negotiation, won, lost)"; return 1 ;;
  esac
  sqlite3 "$DB" "UPDATE deals SET stage='$stage', updated_at=datetime('now') WHERE id=$id;"
  echo "Deal $id moved to stage: $stage"
  sql "SELECT d.*, c.name AS contact, co.name AS company
    FROM deals d LEFT JOIN contacts c ON d.contact_id=c.id LEFT JOIN companies co ON d.company_id=co.id
    WHERE d.id=$id;"
}

list_deals() {
  local stage="${1:-}"
  local contact_id="${2:-}"
  local where=""
  [ -n "$stage" ] && where="WHERE d.stage='$(escape "$stage")'"
  if [ -n "$contact_id" ]; then
    [ -n "$where" ] && where="$where AND" || where="WHERE"
    where="$where d.contact_id=$contact_id"
  fi
  sql "SELECT d.id, d.title, d.amount, d.currency, d.stage, d.expected_close, c.name AS contact, co.name AS company
    FROM deals d LEFT JOIN contacts c ON d.contact_id=c.id LEFT JOIN companies co ON d.company_id=co.id
    $where ORDER BY d.updated_at DESC LIMIT 20;"
}

pipeline_summary() {
  echo "=== Deal Pipeline Summary ==="
  sql "SELECT stage, COUNT(*) AS count, COALESCE(SUM(amount),0) AS total_amount, currency
    FROM deals WHERE stage NOT IN ('won','lost')
    GROUP BY stage, currency ORDER BY
    CASE stage WHEN 'lead' THEN 1 WHEN 'qualified' THEN 2 WHEN 'proposal' THEN 3 WHEN 'negotiation' THEN 4 END;"
  echo ""
  echo "=== Closed Deals ==="
  sql "SELECT stage, COUNT(*) AS count, COALESCE(SUM(amount),0) AS total_amount, currency
    FROM deals WHERE stage IN ('won','lost')
    GROUP BY stage, currency;"
}

# ── Activities ────────────────────────────────────────────────────────────────
log_activity() {
  local type="${1:?type required (call|meeting|email|note)}"
  local summary="${2:?summary required}"
  local contact_id="${3:-NULL}"
  local deal_id="${4:-NULL}"
  case "$type" in
    call|meeting|email|note) ;;
    *) echo "Invalid type: $type (valid: call, meeting, email, note)"; return 1 ;;
  esac
  sqlite3 "$DB" "INSERT INTO activities (type, summary, contact_id, deal_id)
    VALUES ('$type', '$(escape "$summary")', $contact_id, $deal_id);"
  # Update last_contact on the contact
  [ "$contact_id" != "NULL" ] && sqlite3 "$DB" "UPDATE contacts SET last_contact=date('now'), updated_at=datetime('now') WHERE id=$contact_id;"
  local id=$(sqlite3 "$DB" "SELECT last_insert_rowid();")
  echo "Activity logged: $type (id: $id)"
}

recent_activities() {
  local contact_id="${1:-}"
  local limit="${2:-10}"
  local where=""
  [ -n "$contact_id" ] && where="WHERE a.contact_id=$contact_id"
  sql "SELECT a.id, a.type, a.summary, c.name AS contact, d.title AS deal, a.occurred_at
    FROM activities a LEFT JOIN contacts c ON a.contact_id=c.id LEFT JOIN deals d ON a.deal_id=d.id
    $where ORDER BY a.occurred_at DESC LIMIT $limit;"
}

# ── Tasks ─────────────────────────────────────────────────────────────────────
add_task() {
  local title="${1:?title required}"
  local due_date="${2:-}"
  local contact_id="${3:-NULL}"
  local deal_id="${4:-NULL}"
  local priority="${5:-medium}"
  local due_val="NULL"
  [ -n "$due_date" ] && due_val="'$(escape "$due_date")'"
  sqlite3 "$DB" "INSERT INTO tasks (title, due_date, contact_id, deal_id, priority)
    VALUES ('$(escape "$title")', $due_val, $contact_id, $deal_id, '$(escape "$priority")');"
  local id=$(sqlite3 "$DB" "SELECT last_insert_rowid();")
  echo "Task added: $title (id: $id, due: ${due_date:-none}, priority: $priority)"
}

complete_task() {
  local id="${1:?task id required}"
  sqlite3 "$DB" "UPDATE tasks SET status='done' WHERE id=$id;"
  echo "Task $id marked as done"
}

pending_tasks() {
  local contact_id="${1:-}"
  local overdue_only="${2:-}"
  local where="WHERE t.status='todo'"
  [ -n "$contact_id" ] && where="$where AND t.contact_id=$contact_id"
  [ -n "$overdue_only" ] && where="$where AND t.due_date < date('now')"
  sql "SELECT t.id, t.title, t.due_date, t.priority, c.name AS contact, d.title AS deal
    FROM tasks t LEFT JOIN contacts c ON t.contact_id=c.id LEFT JOIN deals d ON t.deal_id=d.id
    $where ORDER BY
    CASE t.priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 WHEN 'low' THEN 3 END,
    t.due_date ASC LIMIT 30;"
}

# ── Dashboard ─────────────────────────────────────────────────────────────────
dashboard() {
  echo "=== CRM Dashboard ==="
  echo ""
  echo "── Contacts ──"
  sql "SELECT COUNT(*) AS total_contacts FROM contacts;"
  echo ""
  echo "── Companies ──"
  sql "SELECT COUNT(*) AS total_companies FROM companies;"
  echo ""
  echo "── Deal Pipeline ──"
  sql "SELECT stage, COUNT(*) AS count, COALESCE(SUM(amount),0) AS total
    FROM deals GROUP BY stage ORDER BY
    CASE stage WHEN 'lead' THEN 1 WHEN 'qualified' THEN 2 WHEN 'proposal' THEN 3 WHEN 'negotiation' THEN 4 WHEN 'won' THEN 5 WHEN 'lost' THEN 6 END;"
  echo ""
  echo "── Overdue Tasks ──"
  sql "SELECT t.id, t.title, t.due_date, t.priority, c.name AS contact
    FROM tasks t LEFT JOIN contacts c ON t.contact_id=c.id
    WHERE t.status='todo' AND t.due_date < date('now')
    ORDER BY t.due_date ASC LIMIT 10;"
  echo ""
  echo "── Recent Activities ──"
  sql "SELECT a.type, a.summary, c.name AS contact, a.occurred_at
    FROM activities a LEFT JOIN contacts c ON a.contact_id=c.id
    ORDER BY a.occurred_at DESC LIMIT 5;"
}

# ── Router ────────────────────────────────────────────────────────────────────
cmd="${1:-help}"
shift 2>/dev/null || true

case "$cmd" in
  init)               init_db ;;
  add-contact)        add_contact "$@" ;;
  find-contacts)      find_contacts "$@" ;;
  update-contact)     update_contact "$@" ;;
  list-contacts)      list_contacts "$@" ;;
  add-company)        add_company "$@" ;;
  find-companies)     find_companies "$@" ;;
  update-company)     update_company "$@" ;;
  add-deal)           add_deal "$@" ;;
  move-deal)          move_deal "$@" ;;
  list-deals)         list_deals "$@" ;;
  pipeline-summary)   pipeline_summary ;;
  log-activity)       log_activity "$@" ;;
  recent-activities)  recent_activities "$@" ;;
  add-task)           add_task "$@" ;;
  complete-task)      complete_task "$@" ;;
  pending-tasks)      pending_tasks "$@" ;;
  dashboard)          dashboard ;;
  help)
    echo "Usage: crm.sh <command> [args...]"
    echo ""
    echo "Commands:"
    echo "  init                                    Initialize CRM database"
    echo "  add-contact <name> [email] [phone] [company] [role] [tags]"
    echo "  find-contacts <query>                   Search contacts by name/email/company/tag"
    echo "  update-contact <id> <field> <value> ...  Update contact fields"
    echo "  list-contacts [limit] [tag]             List contacts"
    echo "  add-company <name> [industry] [website]"
    echo "  find-companies <query>                  Search companies"
    echo "  update-company <id> <field> <value> ... Update company fields"
    echo "  add-deal <title> [contact_id] [company_id] [amount] [stage] [expected_close]"
    echo "  move-deal <id> <stage>                  Move deal to stage"
    echo "  list-deals [stage] [contact_id]         List deals"
    echo "  pipeline-summary                        Pipeline stage summary"
    echo "  log-activity <type> <summary> [contact_id] [deal_id]"
    echo "  recent-activities [contact_id] [limit]  Recent activity log"
    echo "  add-task <title> [due_date] [contact_id] [deal_id] [priority]"
    echo "  complete-task <id>                      Mark task as done"
    echo "  pending-tasks [contact_id] [overdue_only]"
    echo "  dashboard                               Full CRM overview"
    ;;
  *) echo "Unknown command: $cmd (run 'crm.sh help' for usage)"; exit 1 ;;
esac
