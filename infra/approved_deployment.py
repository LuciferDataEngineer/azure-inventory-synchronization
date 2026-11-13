"""Check an approved lab target before invoking Azure CLI with the reviewed dev parameters."""
import argparse
import ipaddress
import json
import os
import re
import subprocess
import uuid


def object_guid(value, label):
    parsed = uuid.UUID(value)
    if parsed.int == 0:
        raise ValueError(f"Set {label} to a nonzero GUID")
    return str(parsed)


def validate_parameters(env):
    if env.get("APPROVAL_REFERENCE", "").strip().upper() in ("", "UNAPPROVED"):
        raise ValueError("An approved deployment and budget reference is required")
    group = env.get("LAB_GROUP", "")
    if not re.fullmatch(r"[A-Za-z0-9_()-](?:[A-Za-z0-9_.()-]{0,88}[A-Za-z0-9_()-])?", group):
        raise ValueError("Set the dedicated resource group name")
    region = env.get("LAB_LOCATION", "")
    if not re.fullmatch(r"[a-z][a-z0-9]+", region):
        raise ValueError("Set the approved canonical Azure region for this dedicated lab")
    subscription = object_guid(env["LAB_SUBSCRIPTION_ID"], "LAB_SUBSCRIPTION_ID")
    ip = ipaddress.IPv4Address(env.get("OPERATOR_IP", ""))
    if not ip.is_global or ip.is_multicast or ip.is_reserved:
        raise ValueError("Operator IPv4 must identify an individual public unicast egress address")
    object_guid(env["SQL_ADMIN_GROUP_ID"], "SQL_ADMIN_GROUP_ID")
    if env["SQL_ADMIN_GROUP_NAME"].strip().upper() in ("", "UNCONFIGURED"):
        raise ValueError("Set the actual SQL administrator group name")
    for variable in ("FUNCTION_IMAGE", "WEB_IMAGE", "MUTATION_IMAGE"):
        if not re.fullmatch(r"[a-z0-9.-]+/[a-z0-9._/-]+@sha256:[0-9a-f]{64}", env.get(variable, "")):
            raise ValueError(f"Set the public digest-pinned image for {variable}")
    return group, region, subscription


def check_target(group_result, group, region, subscription):
    expected_id = f"/subscriptions/{subscription}/resourceGroups/{group}"
    if group_result.get("id", "").casefold() != expected_id.casefold() or group_result.get("location") != region:
        raise ValueError("Resource group, subscription or region differs from approved lab settings")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["what-if", "create"])
    phase = parser.parse_args().phase
    group, region, subscription = validate_parameters(os.environ)
    query = subprocess.check_output(["az", "group", "show", "--name", group, "--subscription", subscription,
                                     "--query", "{id:id,location:location}", "--output", "json"])
    check_target(json.loads(query), group, region, subscription)
    subprocess.run(["az", "deployment", "group", phase, "--subscription", subscription, "--name", "inventory-lab",
                    "--resource-group", group, "--parameters", "infra/dev.bicepparam"], check=True)
