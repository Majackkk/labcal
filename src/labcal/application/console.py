"""Small, scoped console projections. Large file bodies stay behind detail endpoints."""
import json

from .campaigns import campaign_store, list_campaigns, get_campaign, list_items
from ..errors import LabcalError
from ..util import content_hash
from .workspace_catalog import catalog


def console_state(workspace, lab_id=None, campaign_id=None, *, search="", page=1):
    if not isinstance(page, int) or not 1 <= page <= 100000:
        raise LabcalError("PAGE_INVALID", "清单页码无效。")
    laboratories, archives = catalog(workspace)
    result = {"laboratories": laboratories, "archives": archives, "lab_id": lab_id, "campaign_id": campaign_id,
        "campaigns": [], "certificates": [], "reviews": [], "sources": [], "equipment": [], "baselines": [],
        "plans": [], "workbooks": [], "intelligence": [], "totals": {}, "page": page, "page_size": 20, "search": search}
    if not lab_id: return result
    laboratory = next((l for l in result["laboratories"] if l["id"] == lab_id), None)
    if laboratory is None: raise LabcalError("LAB_NOT_FOUND", "实验室尚未建立。")
    result["laboratory_hash"] = content_hash(laboratory)
    store = campaign_store(workspace, lab_id)
    store.enable_lookup_indexes()
    result["campaigns"] = list_campaigns(workspace, lab_id)
    with store.connect() as db:
        result["equipment"] = [{**dict(r), "configuration": json.loads(r["configuration"])} for r in db.execute("SELECT * FROM equipment ORDER BY asset_code")]
        result["unfiled"] = {kind: db.execute(f"SELECT COUNT(*) FROM {table} t WHERE NOT EXISTS (SELECT 1 FROM campaign_links l WHERE l.kind=? AND l.entity_id=t.id)", (kind,)).fetchone()[0]
            for kind, table in [("extraction", "certificate_extractions"), ("workbook", "workbook_imports"), ("review", "certificate_reviews")]}
        if not campaign_id: return result
        result["campaign"] = get_campaign(workspace, lab_id, campaign_id)
        result["totals"] = {r[0]: r[1] for r in db.execute("SELECT kind,COUNT(*) FROM campaign_links WHERE campaign_id=? GROUP BY kind", (campaign_id,))}
        # Query only scoped names/summaries; raw text and page images load on demand.
        pattern = "%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        report_from = " FROM campaign_links l JOIN certificate_extractions e ON e.id=l.entity_id JOIN certificates c ON c.id=e.certificate_id JOIN sources s ON s.id=c.source_id WHERE l.campaign_id=? AND l.kind='extraction'"
        result["report_count"] = db.execute("SELECT COUNT(DISTINCT c.id)" + report_from, (campaign_id,)).fetchone()[0]
        report_filter = report_from + " AND s.name LIKE ? ESCAPE '\\'"
        result["certificate_matches"] = db.execute("SELECT COUNT(DISTINCT c.id)" + report_filter, (campaign_id, pattern)).fetchone()[0]
        result["certificates"] = [dict(r) for r in db.execute("WITH ranked AS (SELECT c.id,c.source_id,s.name,e.id extraction_id,e.payload_hash extraction_hash,e.created_at,COUNT(*) OVER(PARTITION BY c.id) version_count,ROW_NUMBER() OVER(PARTITION BY c.id ORDER BY e.created_at DESC,e.id DESC) position" + report_filter + ") SELECT id,source_id,name,extraction_id,extraction_hash,created_at,version_count FROM ranked WHERE position=1 ORDER BY created_at DESC,extraction_id DESC LIMIT 20 OFFSET ?", (campaign_id, pattern, (page-1)*20))]
        for kind, table, key in [("workbook", "workbook_imports", "workbooks"), ("review", "certificate_reviews", "reviews"), ("intelligence", "intelligence_packets", "intelligence")]:
            rows = db.execute(f"SELECT t.* FROM {table} t JOIN campaign_links l ON l.entity_id=t.id AND l.kind=? WHERE l.campaign_id=? ORDER BY t.created_at DESC LIMIT 50", (kind, campaign_id))
            workbook_by_source = {}
            for row in rows:
                p = json.loads(row["payload"]); item = {"id": row["id"], "created_at": row["created_at"]}
                if kind == "workbook":
                    if p["source_id"] in workbook_by_source:
                        workbook_by_source[p["source_id"]]["previous_versions"].append(item)
                        continue
                    s = db.execute("SELECT name FROM sources WHERE id=?", (p["source_id"],)).fetchone()
                    item.update(name=s[0], candidates=len(p["candidates"]), groups=len({r["group"] for r in p["candidates"]}), previous_versions=[])
                    workbook_by_source[p["source_id"]] = item
                elif kind == "review": item.update(findings=len(p["findings"]), baseline_id=p.get("baseline_id"))
                else:
                    received = db.execute("SELECT COUNT(*) FROM intelligence_results WHERE packet_id=?", (row["id"],)).fetchone()[0]
                    item.update(status="candidate_received" if received else "awaiting_agent_result", provider="project_agent")
                result[key].append(item)
        for row in db.execute("SELECT r.* FROM plan_revisions r JOIN campaign_links l ON l.entity_id=r.plan_id AND l.revision=r.revision AND l.kind='plan' WHERE l.campaign_id=? ORDER BY r.created_at DESC LIMIT 100", (campaign_id,)):
            p = json.loads(row["payload"])
            result["plans"].append({"id": row["plan_id"], "revision": row["revision"], "state": row["state"], "title": p["title"], "equipment_id": p["equipment_id"], "requirements": len(p["requirements"])})
        for row in db.execute("SELECT b.* FROM baselines b JOIN campaign_links l ON l.entity_id=b.id AND l.kind='baseline' WHERE l.campaign_id=?", (campaign_id,)):
            p = json.loads(row["payload"])
            result["baselines"].append({"id": row["id"], "plan_id": p["plan_id"], "revision": p["revision"]})
        result["sources"] = [dict(r) for r in db.execute("SELECT s.id,s.name,s.kind FROM sources s JOIN campaign_links l ON l.entity_id=s.id AND l.kind='source' WHERE l.campaign_id=? AND s.kind IN ('calibration_certificate','verification_certificate','certificate_document') ORDER BY s.created_at DESC LIMIT 50", (campaign_id,))]
    return result


def library_items(workspace, lab_id, kind, *, search="", page=1):
    if not isinstance(page, int) or not 1 <= page <= 100000: raise LabcalError("PAGE_INVALID", "清单页码无效。")
    store = campaign_store(workspace, lab_id)
    with store.connect() as db:
        if kind == "extraction":
            query = "SELECT e.id,e.payload_hash entity_hash,0 revision,s.name FROM certificate_extractions e JOIN certificates c ON c.id=e.certificate_id JOIN sources s ON s.id=c.source_id"
        elif kind == "source":
            query = "SELECT id,sha256 entity_hash,0 revision,name FROM sources WHERE kind IN ('calibration_certificate','verification_certificate','certificate_document','institution_scope','user_plan')"
        elif kind == "plan":
            query = "SELECT r.plan_id id,r.payload_hash entity_hash,r.revision,p.title name FROM plan_revisions r JOIN plans p ON p.id=r.plan_id"
        elif kind == "baseline":
            query = "SELECT b.id,b.payload_hash entity_hash,0 revision,p.title name FROM baselines b JOIN plans p ON p.id=b.plan_id"
        elif kind == "review":
            query = "SELECT id,payload_hash entity_hash,0 revision,'审查任务 '||created_at name FROM certificate_reviews"
        else: raise LabcalError("LIBRARY_KIND_INVALID", "请选择证书、原件、方案、固定基准或审查任务。")
        pattern = "%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        total = db.execute(f"SELECT COUNT(*) FROM ({query}) WHERE name LIKE ? ESCAPE '\\'", (pattern,)).fetchone()[0]
        rows = [dict(r) for r in db.execute(f"SELECT * FROM ({query}) WHERE name LIKE ? ESCAPE '\\' ORDER BY name,id,revision DESC LIMIT 20 OFFSET ?", (pattern, (page-1)*20))]
    return {"items": rows, "total": total, "page": page, "kind": kind}
