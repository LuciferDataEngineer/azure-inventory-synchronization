"""Contract at the Cosmos/SQL boundary. Ignore Cosmos metadata, preserve business fields."""
import hashlib
import json

TEXT_LIMITS = {"tenant_id": 128, "product_id": 128, "sku": 128, "parent_id": 128}
FIELDS = (*TEXT_LIMITS, "stock", "version", "deleted", "changed_at_ms")


def payload(document):
    if not isinstance(document, dict):
        raise ValueError("expected_object")
    result = {}
    for key, limit in TEXT_LIMITS.items():
        val = document.get(key)
        if not isinstance(val, str) or not val.strip() or val != val.strip() or len(val.encode("utf-16le")) > 2 * limit:
            raise ValueError("invalid_" + key)
        result[key] = val
    if document.get("id", result["product_id"]) != result["product_id"]:
        raise ValueError("id_mismatch")
    for key, minimum in (("stock", 0), ("version", 1), ("changed_at_ms", 0)):
        val = document.get(key)
        if type(val) is not int or not minimum <= val <= 2**63 - 1:
            raise ValueError("invalid_" + key)
        result[key] = val
    if type(document.get("deleted")) is not bool:
        raise ValueError("invalid_deleted")
    result["deleted"] = document["deleted"]
    return result


def encoded(document):
    return json.dumps(document, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()


def digest(document):
    return hashlib.sha256(encoded(document)).hexdigest()
