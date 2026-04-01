"""
Neo4j Sandbox Auto-Provisioner for Google Colab
================================================
Spins up a free Neo4j Sandbox instance using just your Sandbox API key.

One-time setup:
  1. Go to https://sandbox.neo4j.com/account/api-key
  2. Generate and copy your API key

Usage in Colab:
  CELL 1:  !pip install requests neo4j
  CELL 2:  Paste this script and run it.
"""

import requests
import time
import json
import os

# ── Configuration ────────────────────────────────────────────────────────────
SANDBOX_API = "https://sandbox-api.neo4j.com"


def _headers(api_key: str) -> dict:
    """Build auth headers using the Sandbox API key."""
    return {
        "Authorization": f"Bearer ApiKey {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


# ── List existing sandboxes ─────────────────────────────────────────────────
def list_sandboxes(api_key: str) -> list:
    """List all running sandbox instances for your account."""
    print("📋 Checking existing sandboxes...")
    resp = requests.get(f"{SANDBOX_API}/sandbox", headers=_headers(api_key))

    if resp.status_code != 200:
        print(f"⚠️  Could not list sandboxes ({resp.status_code}): {resp.text}")
        return []

    sandboxes = resp.json()
    if isinstance(sandboxes, list) and sandboxes:
        print(f"   Found {len(sandboxes)} existing sandbox(es):")
        for sb in sandboxes:
            sb_id = sb.get("sandboxHashKey") or sb.get("sandboxId") or sb.get("id", "?")
            uc = sb.get("usecase", "?")
            status = sb.get("status", "?")
            print(f"   • {uc} | ID: {sb_id} | Status: {status}")
    else:
        print("   No existing sandboxes.")
    return sandboxes if isinstance(sandboxes, list) else []


# ── Create a new sandbox ────────────────────────────────────────────────────
def create_sandbox(api_key: str, usecase: str = "blank-sandbox") -> dict:
    """
    Create a new sandbox instance.

    Common usecases:
      - "blank-sandbox"       → Empty database
      - "movies"              → Movies dataset
      - "recommendations"     → Recommendations dataset
      - "graph-data-science"  → GDS playground
    """
    print(f"🚀 Creating '{usecase}' sandbox...")

    resp = requests.post(
        f"{SANDBOX_API}/sandbox",
        headers=_headers(api_key),
        json={"usecase": usecase},
    )

    if resp.status_code not in (200, 201, 202):
        raise RuntimeError(
            f"Failed to create sandbox ({resp.status_code}).\n"
            f"Response: {resp.text}\n\n"
            "Possible issues:\n"
            "  • You may have hit the sandbox limit (try deleting one first)\n"
            "  • The usecase name may be invalid\n"
            "  • Your API key may be expired or invalid"
        )

    data = resp.json()
    print("✅ Sandbox creation initiated!")
    return data


# ── Wait for sandbox to be ready ────────────────────────────────────────────
def wait_for_ready(api_key: str, sandbox_hash_key: str, timeout: int = 180) -> dict:
    """Poll until the sandbox is running and connection details are available."""
    print("⏳ Waiting for sandbox to be ready", end="", flush=True)
    start = time.time()

    while time.time() - start < timeout:
        resp = requests.get(f"{SANDBOX_API}/sandbox", headers=_headers(api_key))
        if resp.status_code == 200:
            sandboxes = resp.json()
            if isinstance(sandboxes, list):
                for sb in sandboxes:
                    key = (
                        sb.get("sandboxHashKey")
                        or sb.get("sandboxId")
                        or sb.get("id", "")
                    )
                    if str(key) == str(sandbox_hash_key):
                        status = sb.get("status", "").upper()
                        if status in ("RUNNING", "READY", "STARTED"):
                            if sb.get("ip") or sb.get("boltUrl"):
                                print("\n✅ Sandbox is ready!")
                                return sb
        print(".", end="", flush=True)
        time.sleep(5)

    raise TimeoutError(
        f"Sandbox not ready after {timeout}s. "
        "Check https://sandbox.neo4j.com"
    )


# ── Get connection details ───────────────────────────────────────────────────
def get_connection_details(api_key: str, sandbox_hash_key: str) -> dict:
    """Fetch connection details for a specific sandbox."""
    # Try the dedicated connection details endpoint
    resp = requests.get(
        f"{SANDBOX_API}/sandbox/{sandbox_hash_key}/connection",
        headers=_headers(api_key),
    )

    if resp.status_code == 200:
        return resp.json()

    # Fall back to listing all sandboxes and finding ours
    resp = requests.get(f"{SANDBOX_API}/sandbox", headers=_headers(api_key))
    if resp.status_code == 200:
        for sb in resp.json():
            key = (
                sb.get("sandboxHashKey")
                or sb.get("sandboxId")
                or sb.get("id", "")
            )
            if str(key) == str(sandbox_hash_key):
                return sb

    return {}


# ── Extract Bolt URL and password from sandbox data ─────────────────────────
def extract_credentials(sandbox: dict) -> dict:
    """Parse the sandbox response into clean connection credentials."""

    ip = sandbox.get("ip") or sandbox.get("host", "")
    bolt_port = sandbox.get("boltPort") or sandbox.get("bolt_port", 7687)
    password = sandbox.get("password") or sandbox.get("initialPassword", "")
    username = sandbox.get("username", "neo4j")
    sandbox_id = (
        sandbox.get("sandboxHashKey")
        or sandbox.get("sandboxId")
        or sandbox.get("id", "")
    )
    usecase = sandbox.get("usecase", "")
    https_port = sandbox.get("httpsPort") or sandbox.get("https_port", "")
    http_port = sandbox.get("httpPort") or sandbox.get("browser_port", 7474)

    # Build connection URLs
    if sandbox.get("boltUrl"):
        bolt_url = sandbox["boltUrl"]
    elif ip:
        bolt_url = f"bolt://{ip}:{bolt_port}"
    else:
        bolt_url = ""

    browser_url = ""
    if ip and https_port:
        browser_url = f"https://{ip}:{https_port}"
    elif ip and http_port:
        browser_url = f"http://{ip}:{http_port}"

    return {
        "bolt_url": bolt_url,
        "username": username,
        "password": password,
        "browser_url": browser_url,
        "sandbox_id": sandbox_id,
        "usecase": usecase,
        "ip": ip,
        "bolt_port": bolt_port,
        "raw": sandbox,
    }


# ── Terminate a sandbox ─────────────────────────────────────────────────────
def terminate_sandbox(api_key: str, sandbox_hash_key: str):
    """Stop and delete a sandbox (free up your slot)."""
    resp = requests.delete(
        f"{SANDBOX_API}/sandbox/{sandbox_hash_key}",
        headers=_headers(api_key),
    )
    if resp.status_code in (200, 202, 204):
        print(f"🗑️  Sandbox {sandbox_hash_key} terminated.")
    else:
        print(f"⚠️  Failed to terminate ({resp.status_code}): {resp.text}")


# ── Extend sandbox lifetime ─────────────────────────────────────────────────
def extend_sandbox(api_key: str, sandbox_hash_key: str = None):
    """Extend the lifetime of a sandbox (or all sandboxes if key is None)."""
    payload = {}
    if sandbox_hash_key:
        payload["sandboxHashKey"] = sandbox_hash_key

    resp = requests.post(
        f"{SANDBOX_API}/sandbox/extend",
        headers=_headers(api_key),
        json=payload,
    )
    if resp.status_code == 200:
        print("⏰ Sandbox lifetime extended!")
    else:
        print(f"⚠️  Failed to extend ({resp.status_code}): {resp.text}")


# ── Main Flow ────────────────────────────────────────────────────────────────
def provision_neo4j(
    api_key: str,
    usecase: str = "blank-sandbox",
    wait: bool = True,
) -> dict:
    """
    Full provisioning flow. Returns a dict with:
      - bolt_url
      - username
      - password
      - browser_url
      - sandbox_id
    """
    print("=" * 60)
    print("  Neo4j Sandbox Auto-Provisioner (API Key)")
    print("=" * 60)
    print()

    # Show existing sandboxes
    existing = list_sandboxes(api_key)
    print()

    # Create a new sandbox
    result = create_sandbox(api_key, usecase)

    # Extract the sandbox identifier
    if isinstance(result, list):
        sandbox = result[-1] if result else {}
    elif isinstance(result, dict):
        sandbox = result
    else:
        sandbox = {}

    sandbox_id = (
        sandbox.get("sandboxHashKey")
        or sandbox.get("sandboxId")
        or sandbox.get("id", "")
    )

    # If we didn't get an ID from create, re-list to find the new one
    if not sandbox_id:
        print("🔍 Looking up new sandbox...")
        time.sleep(3)
        new_list = list_sandboxes(api_key)
        old_ids = {
            sb.get("sandboxHashKey") or sb.get("sandboxId") or sb.get("id")
            for sb in existing
        }
        for sb in new_list:
            sid = sb.get("sandboxHashKey") or sb.get("sandboxId") or sb.get("id")
            if sid and sid not in old_ids:
                sandbox = sb
                sandbox_id = sid
                break

    if not sandbox_id:
        print("⚠️  Could not determine sandbox ID. Check https://sandbox.neo4j.com")
        return {"raw": result}

    # Wait for it to be ready
    if wait:
        sandbox = wait_for_ready(api_key, sandbox_id)

    # Get connection details (try dedicated endpoint too)
    details = get_connection_details(api_key, sandbox_id)
    if details:
        sandbox.update(details)

    # Extract clean credentials
    creds = extract_credentials(sandbox)

    # Display
    print()
    print("=" * 60)
    print("  ✅ YOUR NEO4J SANDBOX IS READY")
    print("=" * 60)
    print(f"  Bolt URL  : {creds['bolt_url']}")
    print(f"  Username  : {creds['username']}")
    print(f"  Password  : {creds['password']}")
    if creds["browser_url"]:
        print(f"  Browser   : {creds['browser_url']}")
    print(f"  Sandbox ID: {creds['sandbox_id']}")
    print("=" * 60)
    print()

    return creds


# ── Run ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":

    # Set your API key via env var or Colab Secrets:
    #   import os
    #   os.environ["NEO4J_SANDBOX_API_KEY"] = "your-api-key-here"

    api_key = os.environ.get("NEO4J_SANDBOX_API_KEY", "")

    if not api_key:
        print("ℹ️  No API key found. Set it like this:\n")
        print('   import os')
        print('   os.environ["NEO4J_SANDBOX_API_KEY"] = "your-api-key-here"\n')
        print("   Then re-run this cell.\n")
        print("   Get your API key at: https://sandbox.neo4j.com/account/api-key")
    else:
        connection_info = provision_neo4j(api_key)

        # ── Quick test (uncomment to verify) ──
        # from neo4j import GraphDatabase
        # driver = GraphDatabase.driver(
        #     connection_info["bolt_url"],
        #     auth=(connection_info["username"], connection_info["password"])
        # )
        # with driver.session() as session:
        #     result = session.run("RETURN 'Hello from Neo4j Sandbox!' AS msg")
        #     print(result.single()["msg"])
        # driver.close()
