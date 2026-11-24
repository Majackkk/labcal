"""One database per laboratory and a separate shared reference catalogue."""
from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import sqlite3

from ..errors import LabcalError
from ..util import canonical_json, check_lab_id, content_hash, utc_now, ensure_inside


COMMON_SQL = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sources (
 id TEXT PRIMARY KEY, sha256 TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
 code TEXT, name TEXT NOT NULL, object_path TEXT NOT NULL, metadata TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, subject_id TEXT,
 payload TEXT NOT NULL, occurred_at TEXT NOT NULL, previous_hash TEXT NOT NULL,
 event_hash TEXT NOT NULL
);
"""
LAB_SQL = """
CREATE TABLE IF NOT EXISTS equipment (
 id TEXT PRIMARY KEY, asset_code TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
 model TEXT, configuration TEXT NOT NULL, config_hash TEXT NOT NULL,
 configuration_state TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL,
 config_revision INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS plans (
 id TEXT PRIMARY KEY, equipment_id TEXT NOT NULL REFERENCES equipment(id),
 title TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plan_revisions (
 plan_id TEXT NOT NULL REFERENCES plans(id), revision INTEGER NOT NULL,
 payload TEXT NOT NULL, payload_hash TEXT NOT NULL, state TEXT NOT NULL,
 created_at TEXT NOT NULL, PRIMARY KEY(plan_id, revision)
);
CREATE TABLE IF NOT EXISTS evidence_reviews (
 id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, revision INTEGER NOT NULL,
 requirement_id TEXT NOT NULL, source_id TEXT NOT NULL, requirement_hash TEXT NOT NULL,
 source_hash TEXT NOT NULL, record TEXT NOT NULL, created_at TEXT NOT NULL,
 FOREIGN KEY(plan_id, revision) REFERENCES plan_revisions(plan_id, revision)
);
CREATE TABLE IF NOT EXISTS confirmations (
 id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, revision INTEGER NOT NULL,
 payload_hash TEXT NOT NULL, config_hash TEXT NOT NULL, record TEXT NOT NULL,
 created_at TEXT NOT NULL, UNIQUE(plan_id, revision),
 FOREIGN KEY(plan_id, revision) REFERENCES plan_revisions(plan_id, revision)
);
CREATE TABLE IF NOT EXISTS baselines (
 id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, revision INTEGER NOT NULL,
 payload TEXT NOT NULL, payload_hash TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(plan_id, revision),
 FOREIGN KEY(plan_id, revision) REFERENCES plan_revisions(plan_id, revision)
);
CREATE TABLE IF NOT EXISTS runs (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, manifest TEXT NOT NULL,
 created_at TEXT NOT NULL
);
"""

CERTIFICATE_SQL = """
CREATE TABLE IF NOT EXISTS certificates (
 id TEXT PRIMARY KEY, source_id TEXT NOT NULL UNIQUE REFERENCES sources(id),
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS certificate_extractions (
 id TEXT PRIMARY KEY, certificate_id TEXT NOT NULL REFERENCES certificates(id),
 payload TEXT NOT NULL, payload_hash TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS certificate_evidence (
 id TEXT PRIMARY KEY, extraction_id TEXT NOT NULL REFERENCES certificate_extractions(id),
 record TEXT NOT NULL, record_hash TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS institution_evidence (
 id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id),
 record TEXT NOT NULL, record_hash TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS certificate_findings (
 id TEXT PRIMARY KEY, extraction_id TEXT NOT NULL REFERENCES certificate_extractions(id),
 record TEXT NOT NULL, record_hash TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS certificate_reviews (
 id TEXT PRIMARY KEY, payload TEXT NOT NULL, payload_hash TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS finding_resolutions (
 id TEXT PRIMARY KEY, review_id TEXT NOT NULL REFERENCES certificate_reviews(id),
 finding_id TEXT NOT NULL, record TEXT NOT NULL, record_hash TEXT NOT NULL,
 created_at TEXT NOT NULL
);
"""

CAMPAIGN_SQL = """
CREATE TABLE IF NOT EXISTS campaigns (
 id TEXT PRIMARY KEY, record TEXT NOT NULL, record_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS campaign_links (
 campaign_id TEXT NOT NULL REFERENCES campaigns(id), kind TEXT NOT NULL,
 entity_id TEXT NOT NULL, revision INTEGER NOT NULL, entity_hash TEXT NOT NULL,
 created_at TEXT NOT NULL, PRIMARY KEY(campaign_id,kind,entity_id,revision)
);
CREATE INDEX IF NOT EXISTS campaign_entity ON campaign_links(kind,entity_id,revision);
CREATE TABLE IF NOT EXISTS workbook_imports (
 id TEXT PRIMARY KEY, payload TEXT NOT NULL, payload_hash TEXT NOT NULL UNIQUE,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS intelligence_packets (
 id TEXT PRIMARY KEY, payload TEXT NOT NULL, payload_hash TEXT NOT NULL UNIQUE,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS intelligence_results (
 id TEXT PRIMARY KEY, packet_id TEXT NOT NULL REFERENCES intelligence_packets(id),
 record TEXT NOT NULL, record_hash TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL
);
"""

INTAKE_SQL = """
CREATE TABLE IF NOT EXISTS report_associations (
 id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL REFERENCES campaigns(id),
 extraction_id TEXT NOT NULL REFERENCES certificate_extractions(id),
 payload TEXT NOT NULL, payload_hash TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS report_association_lookup ON report_associations(campaign_id,extraction_id,created_at);
CREATE TABLE IF NOT EXISTS report_jobs (
 id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL REFERENCES campaigns(id),
 record TEXT NOT NULL, updated_at TEXT NOT NULL
);
"""

REPORT_CORRECTION_SQL = """
CREATE TABLE IF NOT EXISTS report_corrections (
 id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL REFERENCES campaigns(id),
 extraction_id TEXT NOT NULL REFERENCES certificate_extractions(id),
 record TEXT NOT NULL, record_hash TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS report_correction_lookup ON report_corrections(campaign_id,extraction_id,created_at,id);
"""


class Workspace:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.data = self.root / "data"

    def initialize(self):
        for name in ["inbox", "data/references/objects", "data/laboratories"]:
            (self.root / name).mkdir(parents=True, exist_ok=True)
        store = self.reference_store(create=True)
        with store.connect() as db:
            db.execute("INSERT OR IGNORE INTO meta VALUES (?, ?)", ("schema_version", "1"))
        return {"workspace": str(self.root), "schema_version": 1}

    def reference_store(self, *, create=False):
        return Store(self, None, create=create)

    def lab_store(self, lab_id, *, create=False):
        return Store(self, check_lab_id(lab_id), create=create)

    def labs(self):
        directory = self.data / "laboratories"
        if not directory.exists():
            return []
        result = []
        for folder in sorted(directory.iterdir()):
            if folder.is_dir() and (folder / "lab.sqlite").is_file():
                with self.lab_store(folder.name).connect() as db:
                    row = db.execute("SELECT value FROM meta WHERE key='laboratory'").fetchone()
                    if row:
                        result.append(json.loads(row[0]))
        return result

    def resolve_source(self, lab_id, source_id):
        store = self.reference_store() if source_id.startswith("REF-") else self.lab_store(lab_id)
        with store.connect() as db:
            row = db.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        if not row:
            raise LabcalError("SOURCE_NOT_FOUND", "当前实验室或共享参考库中没有该原件。", {"source_id": source_id})
        record = dict(row)
        record["metadata"] = json.loads(record["metadata"])
        record["scope"] = "reference" if store.lab_id is None else lab_id
        record["absolute_path"] = str(ensure_inside(store.directory / record["object_path"], store.directory / "objects"))
        return record


class Store:
    def __init__(self, workspace, lab_id, *, create=False):
        self.workspace = workspace
        self.lab_id = lab_id
        self.directory = workspace.data / ("references" if lab_id is None else f"laboratories/{lab_id}")
        self.path = self.directory / ("catalog.sqlite" if lab_id is None else "lab.sqlite")
        if not self.path.exists() and not create:
            raise LabcalError("STORE_NOT_FOUND", "实验室或参考目录尚未初始化。", {"lab_id": lab_id})
        if create:
            self.directory.mkdir(parents=True, exist_ok=True)
            (self.directory / "objects").mkdir(exist_ok=True)
            if lab_id:
                from .layout import initialize_layout
                initialize_layout(self)
            with self.connect() as db:
                db.executescript(COMMON_SQL + (LAB_SQL if lab_id else ""))
                db.execute("INSERT OR IGNORE INTO meta VALUES ('schema_version', '1')")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            if self.lab_id and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone():
                row = db.execute("SELECT value FROM meta WHERE key='laboratory'").fetchone()
                if row and json.loads(row[0]).get("id") != self.lab_id:
                    raise LabcalError("LAB_DATABASE_SCOPE_MISMATCH", "运行数据库与选定实验室标识不一致。")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def enable_certificate_storage(self):
        """Additive schema migration; never replaces phase-one tables or facts."""
        if self.lab_id is None:
            raise LabcalError("LAB_REQUIRED", "证书业务需要实验室范围。")
        with self.connect() as db:
            db.executescript(CERTIFICATE_SQL)
            db.execute("INSERT OR REPLACE INTO meta VALUES ('certificate_schema_version','2')")

    def has_certificate_storage(self):
        with self.connect() as db:
            return bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='certificate_reviews'").fetchone())

    def enable_campaign_storage(self):
        if self.lab_id is None:
            raise LabcalError("LAB_REQUIRED", "批次业务需要实验室范围。")
        with self.connect() as db:
            version = db.execute("SELECT value FROM meta WHERE key='campaign_schema_version'").fetchone()
            if version and version[0] == "3": return
        self.enable_certificate_storage()
        with self.connect() as db:
            db.executescript(CAMPAIGN_SQL)
            db.execute("INSERT OR REPLACE INTO meta VALUES ('campaign_schema_version','3')")

    def enable_intake_storage(self):
        self.enable_campaign_storage()
        with self.connect() as db:
            if db.execute("SELECT value FROM meta WHERE key='intake_schema_version'").fetchone(): return
            db.executescript(INTAKE_SQL)
            db.execute("INSERT INTO meta VALUES ('intake_schema_version','1')")

    def enable_lookup_indexes(self):
        """Additive performance migration; never changes business payloads or hashes."""
        self.enable_campaign_storage()
        with self.connect() as db:
            if db.execute("SELECT 1 FROM meta WHERE key='lookup_schema_version' AND value='1'").fetchone(): return
            db.executescript("""
                CREATE INDEX IF NOT EXISTS extraction_by_certificate ON certificate_extractions(certificate_id,created_at DESC,id DESC);
                CREATE INDEX IF NOT EXISTS campaign_kind_created ON campaign_links(campaign_id,kind,created_at DESC,entity_id);
            """)
            db.execute("INSERT OR REPLACE INTO meta VALUES ('lookup_schema_version','1')")

    def enable_report_corrections(self):
        self.enable_intake_storage()
        with self.connect() as db:
            if db.execute("SELECT 1 FROM meta WHERE key='report_correction_schema_version'").fetchone(): return
            db.executescript(REPORT_CORRECTION_SQL)
            db.execute("INSERT OR IGNORE INTO meta VALUES ('report_correction_schema_version','1')")

    def enable_method_storage(self):
        """Independent additive migration for reference facts or lab applications."""
        with self.connect() as db:
            if db.execute("SELECT 1 FROM meta WHERE key='method_schema_version' AND value='2'").fetchone(): return
            if self.lab_id is None:
                db.executescript('''
                    CREATE TABLE IF NOT EXISTS method_packages (
                      id TEXT PRIMARY KEY, standard_code TEXT NOT NULL, edition TEXT NOT NULL,
                      payload TEXT NOT NULL, payload_hash TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL);
                    CREATE INDEX IF NOT EXISTS method_standard ON method_packages(standard_code,edition);
                    CREATE TABLE IF NOT EXISTS method_requirement_index (
                      method_id TEXT NOT NULL REFERENCES method_packages(id), requirement_id TEXT NOT NULL,
                      equipment_category TEXT NOT NULL, parameter TEXT NOT NULL,
                      PRIMARY KEY(method_id,requirement_id));
                    CREATE INDEX IF NOT EXISTS method_parameter ON method_requirement_index(parameter,equipment_category);
                ''')
            else:
                db.executescript('''
                    CREATE TABLE IF NOT EXISTS method_uses (
                      id TEXT PRIMARY KEY, equipment_key TEXT NOT NULL, method_id TEXT NOT NULL,
                      record TEXT NOT NULL, record_hash TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL);
                    CREATE INDEX IF NOT EXISTS method_use_equipment ON method_uses(equipment_key,method_id);
                    CREATE TABLE IF NOT EXISTS method_adoptions (
                      id TEXT PRIMARY KEY, record TEXT NOT NULL, record_hash TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS method_diagnoses (
                      seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
                      campaign_id TEXT NOT NULL REFERENCES campaigns(id), payload TEXT NOT NULL,
                      payload_hash TEXT NOT NULL, record_hash TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL);
                ''')
            db.execute("INSERT OR REPLACE INTO meta VALUES ('method_schema_version','2')")

    @staticmethod
    def event(db, kind, subject_id, payload):
        previous = db.execute("SELECT event_hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        previous_hash = previous[0] if previous else ""
        timestamp = utc_now()
        value = {"kind": kind, "subject_id": subject_id, "payload": payload,
                 "occurred_at": timestamp, "previous_hash": previous_hash}
        db.execute("INSERT INTO events(kind,subject_id,payload,occurred_at,previous_hash,event_hash) VALUES (?,?,?,?,?,?)",
                   (kind, subject_id, canonical_json(payload), timestamp, previous_hash, content_hash(value)))

    def audit_chain(self):
        problems = []
        previous = ""
        with self.connect() as db:
            for row in db.execute("SELECT * FROM events ORDER BY seq"):
                value = {"kind": row["kind"], "subject_id": row["subject_id"],
                         "payload": json.loads(row["payload"]), "occurred_at": row["occurred_at"],
                         "previous_hash": row["previous_hash"]}
                if row["previous_hash"] != previous or content_hash(value) != row["event_hash"]:
                    problems.append({"sequence": row["seq"], "code": "EVENT_CHAIN_CHANGED"})
                previous = row["event_hash"]
        return problems

    def backup_database(self, destination):
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise LabcalError("OUTPUT_EXISTS", "备份文件已经存在。")
        with self.connect() as source:
            target = sqlite3.connect(destination)
            try:
                source.backup(target)
            finally:
                target.close()

