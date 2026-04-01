"""
Neo4j AuraDB Auto-Provisioner for Google Colab
================================================
Spins up a free Neo4j AuraDB instance using the official Aura API.
Authenticates with Client ID + Client Secret — no interactive login.

One-time setup (do this once in the Aura Console):
  1. Go to https://console.neo4j.io
  2. Click your profile (top-right) → Account Details (or API Keys)
  3. Under API Credentials, click Create
  4. Save the Client ID and Client Secret

Usage in Colab:
  CELL 1:  !pip install requests neo4j
  CELL 2:  Set env vars + run this script
"""

import requests
import time
import json
import os
from requests.auth import HTTPBasicAuth

# ── Configuration ────────────────────────────────────────────────────────────
AURA_API = "https://api.neo4j.io"


# ── Auth: get bearer token ──────────────────────────────────────────────────
def get_token(client_id: str, client_secret: str) -> str:
    """Exchange Client ID + Secret for a bearer token (valid 1 hour)."""
    print("🔐 Authenticating with Aura API...")

    resp = requests.post(
        f"{AURA_API}/oauth/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={"grant_type": "client_credentials"},
        auth=HTTPBasicAuth(client_id, client_secret),
    )

    if resp.status_code != 200:
        raise RuntimeError(
            f"Auth failed ({resp.status_code}): {resp.text}\n"
            "Check your Client ID and Client Secret.\n"
            "Generate them at: console.neo4j.io → Account Details → API Credentials"
        )

    token = resp.json().get("access_token")
    print("✅ Authenticated!")
    return token


def _auth_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


# ── Get project/tenant ID ──────────────────────────────────────────────────
def get_project_id(token: str) -> str:
    """Get the first available project (tenant) ID."""
    print("📂 Fetching project info...")

    resp = requests.get(f"{AURA_API}/v1/tenants", headers=_auth_headers(token))
    if resp.status_code != 200:
        raise RuntimeError(f"Failed to list tenants ({resp.status_code}): {resp.text}")

    tenants = resp.json().get("data", [])
    if not tenants:
        raise RuntimeError("No projects found. Check your Aura Console.")

    project = tenants[0]
    print(f"✅ Using project: {project.get('name', 'default')} ({project['id']})")
    return project["id"]


# ── List existing instances ──────────────────────────────────────────────────
def list_instances(token: str, project_id: str) -> list:
    """List all AuraDB instances in a project."""
    resp = requests.get(
        f"{AURA_API}/v1/instances",
        headers=_auth_headers(token),
        params={"tenant_id": project_id},
    )
    if resp.status_code != 200:
        print(f"⚠️  Could not list instances: {resp.text}")
        return []

    instances = resp.json().get("data", [])
    if instances:
        print(f"📋 Found {len(instances)} existing instance(s):")
        for inst in instances:
            print(
                f"   • {inst.get('name', '?')} | "
                f"ID: {inst.get('id', '?')} | "
                f"Status: {inst.get('status', '?')} | "
                f"Type: {inst.get('type', '?')}"
            )
    else:
        print("📋 No existing instances.")
    return instances


# ── Create free instance ─────────────────────────────────────────────────────
def create_instance(
    token: str,
    project_id: str,
    name: str = "colab-graph",
    region: str = "us-central1",
    cloud_provider: str = "gcp",
) -> dict:
    """
    Create a free-tier AuraDB instance.

    Returns dict with id, username, password, connection_url.

    NOTE: The password is ONLY returned at creation time.
    """
    print(f"🚀 Creating free AuraDB instance '{name}'...")

    payload = {
        "version": "5",
        "region": region,
        "memory": "1GB",
        "name": name,
        "type": "free-db",
        "tenant_id": project_id,
        "cloud_provider": cloud_provider,
    }

    resp = requests.post(
        f"{AURA_API}/v1/instances",
        headers=_auth_headers(token),
        json=payload,
    )

    if resp.status_code not in (200, 201, 202):
        error = resp.text
        hints = []
        if resp.status_code == 409 or "already" in error.lower() or "limit" in error.lower():
            hints.append(
                "Free tier allows only 1 instance. Delete existing one first:\n"
                "  delete_instance(token, '<instance_id>')"
            )
        if "region" in error.lower() or "provider" in error.lower():
            hints.append(
                "Try different region/provider combos:\n"
                "  AWS:  us-east-1, eu-west-1\n"
                "  GCP:  us-east1, europe-west1"
            )
        hint_str = "\n".join(hints)
        raise RuntimeError(
            f"Failed to create instance ({resp.status_code}).\n"
            f"Response: {error}\n\n{hint_str}"
        )

    data = resp.json().get("data", resp.json())

    info = {
        "instance_id": data.get("id", ""),
        "connection_url": data.get("connection_url", ""),
        "username": data.get("username", "neo4j"),
        "password": data.get("password", ""),
    }

    print(f"✅ Instance created! ID: {info['instance_id']}")
    print(f"   ⚠️  Save the password — it is only shown once!")
    return info


# ── Wait for RUNNING ─────────────────────────────────────────────────────────
def wait_for_running(token: str, instance_id: str, timeout: int = 300) -> dict:
    """Poll until instance status is 'running'."""
    print("⏳ Waiting for instance to be ready", end="", flush=True)
    start = time.time()

    while time.time() - start < timeout:
        resp = requests.get(
            f"{AURA_API}/v1/instances/{instance_id}",
            headers=_auth_headers(token),
        )
        if resp.status_code == 200:
            data = resp.json().get("data", resp.json())
            status = data.get("status", "").lower()
            if status == "running":
                print(f"\n✅ Instance is running!")
                return data
            if status in ("destroying", "destroyed", "failed"):
                raise RuntimeError(f"Instance entered bad state: {status}")
        print(".", end="", flush=True)
        time.sleep(10)

    raise TimeoutError(
        f"Instance not ready after {timeout}s. Check https://console.neo4j.io"
    )


# ── Delete instance ──────────────────────────────────────────────────────────
def delete_instance(token: str, instance_id: str):
    """Delete an AuraDB instance (frees up your free-tier slot)."""
    resp = requests.delete(
        f"{AURA_API}/v1/instances/{instance_id}",
        headers=_auth_headers(token),
    )
    if resp.status_code in (200, 202, 204):
        print(f"🗑️  Instance {instance_id} deletion initiated.")
    else:
        print(f"⚠️  Failed to delete ({resp.status_code}): {resp.text}")


# ── Main Flow ────────────────────────────────────────────────────────────────
def provision_neo4j(
    client_id: str,
    client_secret: str,
    instance_name: str = "colab-graph",
    region: str = "us-central1",
    cloud_provider: str = "gcp",
) -> dict:
    """
    Full provisioning flow. Returns dict with:
      - bolt_url:   neo4j+s://xxxxx.databases.neo4j.io
      - username:   neo4j
      - password:   <generated>
      - instance_id
      - token       (reuse for management calls)
    """
    print("=" * 60)
    print("  Neo4j AuraDB Auto-Provisioner")
    print("=" * 60)
    print()

    # 1. Authenticate
    token = get_token(client_id, client_secret)

    # 2. Get project
    project_id = get_project_id(token)

    # 3. Show existing instances
    list_instances(token, project_id)
    print()

    # 4. Create instance
    instance = create_instance(token, project_id, instance_name, region, cloud_provider)

    # 5. Wait for it to spin up
    wait_for_running(token, instance["instance_id"])

    # 6. Display results
    bolt_url = instance["connection_url"]

    print()
    print("=" * 60)
    print("  ✅ YOUR NEO4J AURADB IS READY")
    print("=" * 60)
    print(f"  Bolt URL    : {bolt_url}")
    print(f"  Username    : {instance['username']}")
    print(f"  Password    : {instance['password']}")
    print(f"  Instance ID : {instance['instance_id']}")
    print("=" * 60)
    print()

    return {
        "bolt_url": bolt_url,
        "username": instance["username"],
        "password": instance["password"],
        "instance_id": instance["instance_id"],
        "token": token,
    }


# ── Run ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":

    # ── Set your credentials (pick one method) ──────────────────────────
    #
    # METHOD A — Environment variables (recommended):
    #   import os
    #   os.environ["NEO4J_CLIENT_ID"] = "your_client_id"
    #   os.environ["NEO4J_CLIENT_SECRET"] = "your_client_secret"
    #
    # METHOD B — Colab Secrets (most secure):
    #   Add NEO4J_CLIENT_ID and NEO4J_CLIENT_SECRET in the key icon sidebar,
    #   then:
    #   from google.colab import userdata
    #   os.environ["NEO4J_CLIENT_ID"] = userdata.get("NEO4J_CLIENT_ID")
    #   os.environ["NEO4J_CLIENT_SECRET"] = userdata.get("NEO4J_CLIENT_SECRET")

    cid = os.environ.get("NEO4J_CLIENT_ID", "")
    csecret = os.environ.get("NEO4J_CLIENT_SECRET", "")

    if not cid or not csecret:
        print("ℹ️  No credentials found. Set them like this:\n")
        print('   import os')
        print('   os.environ["NEO4J_CLIENT_ID"] = "your_client_id"')
        print('   os.environ["NEO4J_CLIENT_SECRET"] = "your_client_secret"\n')
        print("   Then re-run this cell.\n")
        print("   To get API credentials:")
        print("   1. Go to https://console.neo4j.io")
        print("   2. Profile (top-right) → Account Details → API Credentials → Create")
        print("   3. Save the Client ID and Client Secret")
    else:
        connection_info = provision_neo4j(cid, csecret, wait=False)

        # ── Quick connectivity test (uncomment to use) ──
        # from neo4j import GraphDatabase
        # driver = GraphDatabase.driver(
        #     connection_info["bolt_url"],
        #     auth=(connection_info["username"], connection_info["password"])
        # )
        # with driver.session() as session:
        #     msg = session.run("RETURN 'Hello from AuraDB!' AS msg").single()["msg"]
        #     print(msg)
        # driver.close()
