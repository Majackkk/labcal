"""Engineering diagnostics, separate from laboratory technical approval."""
SCHEMAS = {"storage-audit.v1": {
    "type":"object", "required":["schema_version","lab_id","campaign_id","status","technical_approval","counts","issues","query_ms"],
    "properties": {"schema_version":{"const":"1.0"}, "lab_id":{"type":"string","minLength":1},
        "campaign_id":{"type":["string","null"]}, "status":{"enum":["passed","warnings","errors"]},
        "technical_approval":{"const":False}, "counts":{"type":"object"}, "query_ms":{"type":"number","minimum":0},
        "issues":{"type":"array","items":{"type":"object","required":["severity","code","message"],
            "properties":{"severity":{"enum":["error","warning","pending"]},"code":{"type":"string"},"message":{"type":"string"},"path":{"type":"string"}},"additionalProperties":False}}},
    "additionalProperties":False}}
