"""Credential-free Bicep build and parameter-contract check with non-deployable example inputs."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bicep", default="bicep")
    cli = parser.parse_args().bicep
    directory = Path(__file__).resolve().parent
    inputs = {
        "LAB_LOCATION": "australiaeast", "SQL_ADMIN_GROUP_NAME": "Fixture only",
        "SQL_ADMIN_GROUP_ID": "00000000-0000-0000-0000-000000000001", "OPERATOR_IP": "192.0.2.1",
        **{key: f"registry.invalid/test/{key.lower()}@sha256:" + "a"*64
           for key in ("FUNCTION_IMAGE", "MUTATION_IMAGE", "WEB_IMAGE")},
    }
    env = {**os.environ, **inputs}
    with tempfile.TemporaryDirectory() as temporary:
        build = Path(temporary)
        subprocess.run([cli, "build", "main.bicep", "--outfile", str(build/"template.json")],
                       cwd=directory, env=env, check=True)
        target = json.loads((build/"template.json").read_text())
        if target["parameters"]["deploymentStage"]["allowedValues"] != ["dev"]:
            raise RuntimeError("Production must be refused by the actual ARM template")
        subprocess.run([cli, "build-params", "dev.bicepparam", "--outfile", str(build/"dev.json")],
                       cwd=directory, env=env, check=True)
        parameters = json.loads((build/"dev.json").read_text())["parameters"]
        expected = {"deploymentStage": "dev", "location": inputs["LAB_LOCATION"], "operatorIp": inputs["OPERATOR_IP"],
                    "sqlAdminGroupObjectId": inputs["SQL_ADMIN_GROUP_ID"], "sqlAdminGroupName": inputs["SQL_ADMIN_GROUP_NAME"],
                    "functionImage": inputs["FUNCTION_IMAGE"], "dashboardImage": inputs["WEB_IMAGE"],
                    "mutationImage": inputs["MUTATION_IMAGE"]}
        if {key: value["value"] for key, value in parameters.items()} != expected:
            raise RuntimeError("Compiled development parameters differ from expected environment bindings")
        prod = subprocess.run([cli, "build-params", "prod.bicepparam", "--outfile", str(build/"prod.json")],
                              cwd=directory, env=env, capture_output=True, text=True)
        if prod.returncode == 0 or "'prod'" not in prod.stderr or "'dev'" not in prod.stderr or "BCP033" not in prod.stderr:
            raise RuntimeError(f"Production guard did not produce the expected compiler error:\n{prod.stderr}")
        print("Compiled inventory template and dev parameters; production stage rejected by the template contract.")


if __name__ == "__main__":
    main()
