"""Operasyonel durum ve hafıza — SQLite. İş verisi (Excel) buraya taşınmaz;
burada yalnızca aksiyon durumu, tamamlanan/ertelenen/yok sayılan öneriler,
takip geçmişi ve yapılandırılmış hafıza tutulur."""
import sqlite3, os, hashlib, json, datetime as dt

DB_PATH = os.environ.get("AGENT_DB", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "agent_state.db"))


def _conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with _conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS actions(
            action_key TEXT PRIMARY KEY,   -- kategori+entity+content hash
            generated_at TEXT, category TEXT, entity_type TEXT, entity_id TEXT,
            priority TEXT, score REAL, title TEXT, content_hash TEXT,
            status TEXT DEFAULT 'open',    -- open|completed|deferred|ignored
            completed_at TEXT, defer_until TEXT, notes TEXT)""")
        # eski db'ler için güvenli göç
        try: c.execute("ALTER TABLE actions ADD COLUMN defer_until TEXT")
        except Exception: pass
        c.execute("""CREATE TABLE IF NOT EXISTS action_history(
            id INTEGER PRIMARY KEY AUTOINCREMENT, action_key TEXT, ts TEXT,
            event TEXT, detail TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS memory(
            id INTEGER PRIMARY KEY AUTOINCREMENT, entity_type TEXT, entity_id TEXT,
            ts TEXT, kind TEXT,            -- contacted|offer|outcome|followup|rec_accepted|rec_rejected
            detail TEXT, next_followup TEXT)""")
        # AI çağrı gözlemi: YALNIZCA meta veri. Prompt, kanıt paketi, model cevabı ve anahtar SAKLANMAZ.
        c.execute("""CREATE TABLE IF NOT EXISTS ai_calls(
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, task TEXT, attempt INTEGER,
            provider TEXT, model TEXT, success INTEGER, error_category TEXT,
            latency_ms INTEGER, input_tokens INTEGER, output_tokens INTEGER)""")


def content_key(category, entity_id, content_hash):
    return f"{category}:{entity_id}:{content_hash[:8]}"


def hash_facts(facts):
    return hashlib.sha256(json.dumps(facts, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def upsert_actions(actions):
    """Yeni aksiyonları ekler. Aynı içerik (hash) tamamlanmışsa TEKRAR AÇMAZ."""
    now = dt.datetime.now().isoformat(timespec="seconds")
    added, skipped = 0, 0
    with _conn() as c:
        for a in actions:
            ch = hash_facts(a["facts"])
            key = content_key(a["category"], a["entity_id"], ch)
            row = c.execute("SELECT status FROM actions WHERE action_key=?", (key,)).fetchone()
            if row is not None:
                skipped += 1
                continue
            c.execute("""INSERT INTO actions(action_key,generated_at,category,entity_type,entity_id,
                         priority,score,title,content_hash,status) VALUES(?,?,?,?,?,?,?,?,?, 'open')""",
                      (key, now, a["category"], a["entity_type"], a["entity_id"],
                       a["priority"], a["score"], a["title"], ch))
            c.execute("INSERT INTO action_history(action_key,ts,event,detail) VALUES(?,?,?,?)",
                      (key, now, "generated", a["title"]))
            added += 1
    return {"added": added, "skipped_existing": skipped}


def set_status(action_key, status, notes=None, until=None):
    assert status in ("open", "completed", "deferred", "ignored")
    now = dt.datetime.now().isoformat(timespec="seconds")
    with _conn() as c:
        c.execute("UPDATE actions SET status=?, completed_at=?, defer_until=?, notes=? WHERE action_key=?",
                  (status, now if status == "completed" else None, until, notes, action_key))
        c.execute("INSERT INTO action_history(action_key,ts,event,detail) VALUES(?,?,?,?)",
                  (action_key, now, f"status:{status}", notes or ""))


def get_statuses():
    with _conn() as c:
        return {r["action_key"]: dict(r) for r in c.execute("SELECT * FROM actions")}


def add_memory(entity_type, entity_id, kind, detail, next_followup=None):
    now = dt.datetime.now().isoformat(timespec="seconds")
    with _conn() as c:
        c.execute("""INSERT INTO memory(entity_type,entity_id,ts,kind,detail,next_followup)
                     VALUES(?,?,?,?,?,?)""", (entity_type, entity_id, now, kind, detail, next_followup))


def get_memory(entity_type, entity_id):
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM memory WHERE entity_type=? AND entity_id=? ORDER BY ts DESC",
            (entity_type, entity_id))]


def is_hidden(row, today=None):
    """Tamamlanan/yok sayılan gizli; ertelenen 'until' tarihine kadar gizli."""
    today = today or dt.date.today().isoformat()
    st = row["status"] if isinstance(row, dict) else row
    if st in ("completed", "ignored"):
        return True
    if st == "deferred":
        du = row.get("defer_until") if isinstance(row, dict) else None
        return bool(du and du > today)
    return False


def record_ai_call(task, attempt=1, provider=None, model=None, success=False, error_category=None,
                   latency_ms=None, input_tokens=None, output_tokens=None, **_ignored):
    """Metadata only — never prompts, packets, responses or keys."""
    now = dt.datetime.now().isoformat(timespec="seconds")
    with _conn() as c:
        c.execute("""INSERT INTO ai_calls(ts,task,attempt,provider,model,success,error_category,
                     latency_ms,input_tokens,output_tokens) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                  (now, task, attempt, provider, model, int(bool(success)), error_category,
                   latency_ms, input_tokens, output_tokens))


def recent_ai_calls(limit=50):
    with _conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM ai_calls ORDER BY id DESC LIMIT ?", (limit,))]
