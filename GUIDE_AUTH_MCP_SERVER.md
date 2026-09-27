# Guide: Enabling Entra ID Authentication for an Azure App Service MCP Server

This guide documents the end-to-end steps to secure a **FastMCP (Model Context Protocol)** server running on **Azure App Service** with **Microsoft Entra ID** authentication, and preauthorize **Azure AI Foundry** agent identities to call it.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Add MCP Server to FastAPI App](#2-add-mcp-server-to-fastapi-app)
3. [Deploy to Azure App Service](#3-deploy-to-azure-app-service)
4. [Create Entra ID App Registration](#4-create-entra-id-app-registration)
5. [Enable App Service Authentication (EasyAuth)](#5-enable-app-service-authentication-easyauth)
6. [Configure Protected Resource Metadata (PRM)](#6-configure-protected-resource-metadata-prm)
7. [Preauthorize Foundry Agent Identities](#7-preauthorize-foundry-agent-identities)
8. [Verification & Testing](#8-verification--testing)
9. [Troubleshooting](#9-troubleshooting)
10. [Test Cases](#10-test-cases)

---

## 1. Prerequisites

- Azure subscription
- Azure CLI (`az`) and Azure Developer CLI (`azd`) installed
- A FastAPI application deployed (or ready to deploy) to Azure App Service
- Python 3.11+ with `mcp[cli]` package
- Azure AI Foundry project with an agent configured

**Key identifiers used throughout this guide** (replace with your own):

| Item | Value |
|------|-------|
| Subscription ID | `1577b43a-6b5c-4c1d-845e-2e50d692189b` |
| Resource Group | `saiyo-rg` |
| App Service Name | `saiyo-rjdd6dwlkawae-app-service` |
| Tenant ID | `6907edd8-11e5-421c-8f84-a3c0bd847a11` |
| App Registration Name | `saiyo-mcp-server-auth` |
| App (Client) ID | `428b00cd-3f42-439a-bd47-15b287a6ef1e` |
| App Object ID | `5734980a-fe93-46a7-8a8c-c9fc70bce637` |
| Service Principal ID | `9d820733-4882-4aa9-94d2-4a1feb5e79b2` |
| Agent Identity (mslearnagent) | `2286091f-3967-4486-a80f-19f3cfe66721` |
| Project Identity | `d60af655-ccb3-4b23-88c3-346eb5558280` |

---

## 2. Add MCP Server to FastAPI App

### 2.1 Add `mcp[cli]` to dependencies

In `pyproject.toml`:
```toml
dependencies = [
    "mcp[cli]",
    # ... other deps
]
```

### 2.2 Create `mcp_server.py`

Create `src/fastapi_app/mcp_server.py`:

```python
import asyncio
import contextlib
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP
from sqlalchemy.sql import func
from sqlmodel import Session, select

from .models import Restaurant, Review, engine

# stateless_http=True is required for mounting under FastAPI
mcp = FastMCP("RestaurantReviewsMCP", stateless_http=True)

# Lifespan context manager - starts/stops MCP session manager with the FastAPI app
@asynccontextmanager
async def mcp_lifespan(app):
    async with contextlib.AsyncExitStack() as stack:
        await stack.enter_async_context(mcp.session_manager.run())
        yield

# Define your MCP tools with @mcp.tool() decorator
@mcp.tool()
async def list_restaurants_mcp() -> list[dict]:
    """List restaurants with their average rating and review count."""
    def sync():
        with Session(engine) as session:
            # ... your DB query logic
            pass
    return await asyncio.to_thread(sync)

# ... more tools
```

### 2.3 Mount MCP in `app.py`

```python
from .mcp_server import mcp, mcp_lifespan

app = FastAPI(lifespan=mcp_lifespan)
app.mount("/mcp", mcp.streamable_http_app())
```

### 2.4 Ensure gunicorn worker has `lifespan: "on"`

In `my_uvicorn_worker.py`, the MCP session manager requires lifespan events:

```python
class MyUvicornWorker(UvicornWorker):
    CONFIG_KWARGS = {
        "loop": "asyncio",
        "http": "auto",
        "lifespan": "on",      # CRITICAL: must be "on" for MCP
        "log_config": logconfig_dict,
    }
```

> **Without `"lifespan": "on"`, the MCP session manager won't start in production (gunicorn), and all MCP requests will fail.**

---

## 3. Deploy to Azure App Service

```bash
azd auth login --use-device-code
azd up
```

### Verify MCP endpoint (before auth)

```bash
# Should return JSON-RPC response
curl -X POST https://<APP_NAME>.azurewebsites.net/mcp/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
      "protocolVersion": "2025-03-26",
      "capabilities": {},
      "clientInfo": {"name": "test", "version": "1.0"}
    }
  }'
```

---

## 4. Create Entra ID App Registration

### 4.1 Create the app registration

```bash
az ad app create --display-name "saiyo-mcp-server-auth" \
  --sign-in-audience AzureADMyOrg
```

Save the output `appId` (client ID) and `id` (object ID).

### 4.2 Add an Application ID URI

```bash
APP_CLIENT_ID="428b00cd-3f42-439a-bd47-15b287a6ef1e"
az ad app update --id $APP_CLIENT_ID \
  --identifier-uris "api://$APP_CLIENT_ID"
```

### 4.3 Add a delegated permission scope (`user_impersonation`)

```bash
az rest --method PATCH \
  --uri "https://graph.microsoft.com/v1.0/applications/<APP_OBJECT_ID>" \
  --headers "Content-Type=application/json" \
  --body '{
    "api": {
      "oauth2PermissionScopes": [{
        "adminConsentDescription": "Allow the application to access the MCP server on behalf of the signed-in user.",
        "adminConsentDisplayName": "Access MCP Server",
        "id": "<GENERATE-A-UUID>",
        "isEnabled": true,
        "type": "User",
        "userConsentDescription": "Allow the application to access the MCP server on your behalf.",
        "userConsentDisplayName": "Access MCP Server",
        "value": "user_impersonation"
      }]
    }
  }'
```

### 4.4 Add an Application role (`MCP.Access`) for client_credentials flow

This is **required** for managed identity / service principal access (like Foundry agents):

```bash
az rest --method PATCH \
  --uri "https://graph.microsoft.com/v1.0/applications/<APP_OBJECT_ID>" \
  --headers "Content-Type=application/json" \
  --body '{
    "appRoles": [{
      "id": "<GENERATE-A-UUID>",
      "allowedMemberTypes": ["Application"],
      "displayName": "MCP.Access",
      "description": "Allow application to access MCP server",
      "isEnabled": true,
      "value": "MCP.Access"
    }]
  }'
```

### 4.5 Create a client secret

```bash
az ad app credential reset --id $APP_CLIENT_ID --append
```

Save the `password` — you'll need it for App Service auth config.

### 4.6 Create the Service Principal

> **This step is often missed!** Without a service principal, app role assignments will fail.

```bash
az ad sp create --id $APP_CLIENT_ID
```

Or via Graph API:
```bash
az rest --method POST \
  --uri "https://graph.microsoft.com/v1.0/servicePrincipals" \
  --headers "Content-Type=application/json" \
  --body '{"appId": "'$APP_CLIENT_ID'"}'
```

---

## 5. Enable App Service Authentication (EasyAuth)

### 5.1 Set the client secret as an app setting

```bash
az webapp config appsettings set \
  --name <APP_NAME> --resource-group <RG> \
  --settings MICROSOFT_PROVIDER_AUTHENTICATION_SECRET="<CLIENT_SECRET>"
```

### 5.2 Configure EasyAuth via ARM API

```bash
SUBSCRIPTION="1577b43a-6b5c-4c1d-845e-2e50d692189b"
RG="saiyo-rg"
APP="saiyo-rjdd6dwlkawae-app-service"
APP_CLIENT_ID="428b00cd-3f42-439a-bd47-15b287a6ef1e"
TENANT_ID="6907edd8-11e5-421c-8f84-a3c0bd847a11"

# Get a management token
TOKEN=$(az account get-access-token --resource https://management.azure.com --query accessToken -o tsv)

# PUT the auth config
curl -X PUT \
  "https://management.azure.com/subscriptions/$SUBSCRIPTION/resourceGroups/$RG/providers/Microsoft.Web/sites/$APP/config/authsettingsV2?api-version=2024-04-01" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
  "properties": {
    "platform": {
      "enabled": true,
      "runtimeVersion": "~2"
    },
    "globalValidation": {
      "requireAuthentication": true,
      "unauthenticatedClientAction": "Return401",
      "redirectToProvider": "azureActiveDirectory"
    },
    "identityProviders": {
      "azureActiveDirectory": {
        "enabled": true,
        "registration": {
          "openIdIssuer": "https://sts.windows.net/'$TENANT_ID'/v2.0",
          "clientId": "'$APP_CLIENT_ID'",
          "clientSecretSettingName": "MICROSOFT_PROVIDER_AUTHENTICATION_SECRET"
        },
        "validation": {
          "allowedAudiences": [
            "api://'$APP_CLIENT_ID'",
            "'$APP_CLIENT_ID'"
          ],
          "jwtClaimChecks": {
            "allowedClientApplications": [
              "<AGENT_IDENTITY_ID>",
              "<PROJECT_IDENTITY_ID>"
            ]
          }
        }
      }
    },
    "login": {
      "tokenStore": {
        "enabled": true
      }
    }
  }
}'
```

**Key settings explained:**

| Setting | Value | Purpose |
|---------|-------|---------|
| `runtimeVersion` | `~2` | **Must be `~2`** — version `~1` doesn't properly enforce auth |
| `unauthenticatedClientAction` | `Return401` | Returns 401 instead of redirecting to login page |
| `allowedAudiences` | `api://<clientId>`, `<clientId>` | Accept tokens targeting either audience format |
| `allowedClientApplications` | Agent & Project IDs | Only these clients can call the API |
| `clientSecretSettingName` | `MICROSOFT_PROVIDER_AUTHENTICATION_SECRET` | References the app setting holding the secret |

### 5.3 Restart the App Service

After changing auth config, restart to ensure it takes effect:

```bash
az webapp stop --name $APP --resource-group $RG
sleep 10
az webapp start --name $APP --resource-group $RG
```

### 5.4 Verify auth is enforced

```bash
# Should return 401
curl -s -o /dev/null -w "%{http_code}" "https://$APP.azurewebsites.net/"
```

---

## 6. Configure Protected Resource Metadata (PRM)

PRM tells MCP clients (like Foundry) how to authenticate. It's served automatically by EasyAuth when configured with the `WEBSITE_AUTH_PRM_DEFAULT_WITH_SCOPES` app setting.

```bash
az webapp config appsettings set \
  --name $APP --resource-group $RG \
  --settings WEBSITE_AUTH_PRM_DEFAULT_WITH_SCOPES="api://$APP_CLIENT_ID/user_impersonation"
```

### Verify PRM endpoint

```bash
# This endpoint is served WITHOUT auth (by design)
curl -s "https://$APP.azurewebsites.net/.well-known/oauth-protected-resource"
```

Expected response:
```json
{
  "resource": "https://<APP_NAME>.azurewebsites.net",
  "authorization_servers": [
    "https://login.microsoftonline.com/<TENANT_ID>/v2.0"
  ],
  "scopes_supported": [
    "api://<CLIENT_ID>/user_impersonation"
  ]
}
```

---

## 7. Preauthorize Foundry Agent Identities

Azure AI Foundry agents use **managed identities** (type `ServiceIdentity`) that authenticate via **client_credentials** flow. They need:

1. An **app role** on your app registration (not a delegated scope)
2. An **app role assignment** granting them that role
3. To be listed in EasyAuth's **`allowedClientApplications`**

### 7.1 Look up Foundry identity details

```bash
# Get the agent and project identity details
az rest --method GET \
  --uri "https://graph.microsoft.com/v1.0/servicePrincipals/<AGENT_PRINCIPAL_ID>?$select=appId,displayName,servicePrincipalType"

az rest --method GET \
  --uri "https://graph.microsoft.com/v1.0/servicePrincipals/<PROJECT_PRINCIPAL_ID>?$select=appId,displayName,servicePrincipalType"
```

For `ServiceIdentity` types, `appId == principalId`.

### 7.2 Grant app role assignments

```bash
OUR_SP_ID="9d820733-4882-4aa9-94d2-4a1feb5e79b2"  # SP of your app registration
ROLE_ID="d3f07651-a73f-45cd-b8a3-73f0304890de"      # MCP.Access role ID

# Agent identity
az rest --method POST \
  --uri "https://graph.microsoft.com/v1.0/servicePrincipals/<AGENT_PID>/appRoleAssignments" \
  --headers "Content-Type=application/json" \
  --body '{
    "principalId": "<AGENT_PID>",
    "resourceId": "'$OUR_SP_ID'",
    "appRoleId": "'$ROLE_ID'"
  }'

# Project identity
az rest --method POST \
  --uri "https://graph.microsoft.com/v1.0/servicePrincipals/<PROJECT_PID>/appRoleAssignments" \
  --headers "Content-Type=application/json" \
  --body '{
    "principalId": "<PROJECT_PID>",
    "resourceId": "'$OUR_SP_ID'",
    "appRoleId": "'$ROLE_ID'"
  }'
```

### 7.3 Note on `preAuthorizedApplications`

`ServiceIdentity` type principals **cannot** be added to the app registration's `api.preAuthorizedApplications` — you'll get an `InvalidAppId` error. This is expected. The **app role assignment + EasyAuth `allowedClientApplications`** combination is the correct approach for managed identities.

---

## 8. Verification & Testing

### 8.1 Verify EasyAuth configuration

```python
import requests, json

TOKEN = "<management-api-token>"
url = f"https://management.azure.com/subscriptions/{SUB}/resourceGroups/{RG}/providers/Microsoft.Web/sites/{APP}/config/authsettingsV2?api-version=2024-04-01"
resp = requests.get(url, headers={"Authorization": f"Bearer {TOKEN}"})
aad = resp.json()['properties']['identityProviders']['azureActiveDirectory']

# Check these are populated:
print(aad['validation']['allowedAudiences'])
# ['api://428b00cd-...', '428b00cd-...']

print(aad['validation']['jwtClaimChecks']['allowedClientApplications'])
# ['2286091f-...', 'd60af655-...']
```

### 8.2 Verify app role assignments

```python
for pid in [AGENT_PID, PROJECT_PID]:
    resp = requests.get(
        f"https://graph.microsoft.com/v1.0/servicePrincipals/{pid}/appRoleAssignments",
        headers={"Authorization": f"Bearer {GRAPH_TOKEN}"}
    )
    for a in resp.json()['value']:
        print(f"{a['principalDisplayName']} -> {a['resourceDisplayName']}: {a['appRoleId']}")
```

### 8.3 Check Entra ID sign-in logs

In the **Azure Portal** → **Microsoft Entra ID** → **Sign-in logs** → **Service principal sign-ins**:
- Filter by **Application** = your Foundry identity name
- Look for **Status: Success** with **Resource** = `saiyo-mcp-server-auth`

### 8.4 Check Foundry agent tool enumeration

In Azure AI Foundry, run the agent. A successful `mcp_list_tools` span shows:
```json
{
  "status": {"status_code": "OK"},
  "attributes": {
    "server_label": "connect2mcp",
    "tools": [
      {"name": "list_restaurants_mcp", ...},
      {"name": "get_details_mcp", ...},
      ...
    ]
  }
}
```

---

## 9. Troubleshooting

### "Initialization timed out" from Foundry

**Possible causes:**

| Cause | How to check | Fix |
|-------|-------------|-----|
| No service principal for app registration | `az ad sp list --filter "appId eq '<CLIENT_ID>'"` returns empty | `az ad sp create --id <CLIENT_ID>` |
| No app role defined | Check `appRoles` on app registration | Add `MCP.Access` app role (Section 4.4) |
| No role assignment for Foundry identities | Check `appRoleAssignments` per SP | Grant role (Section 7.2) |
| Missing `allowedClientApplications` in EasyAuth | Check auth config via ARM API | Add identity IDs (Section 5.2) |
| `runtimeVersion` is `~1` | Check auth config | Change to `~2` and restart app |
| `lifespan` is `"off"` in gunicorn worker | Check `my_uvicorn_worker.py` | Set to `"on"` and redeploy |
| App cold start timeout | Check App Service logs | Scale up or use Always On |

### Reading App Service logs

```python
# Via Kudu API
import requests

KUDU_TOKEN = "<management-token>"
headers = {"Authorization": f"Bearer {KUDU_TOKEN}"}

# List log files
resp = requests.get(f"https://{APP}.scm.azurewebsites.net/api/vfs/LogFiles/", headers=headers)
for f in resp.json():
    print(f['name'], f.get('size'))

# Read specific logs
for log_name in ['_default_docker.log', '_easyauth_docker.log', '_docker.log']:
    # Find the matching file and read it
    pass

# Application diagnostics
resp = requests.get(
    f"https://{APP}.scm.azurewebsites.net/api/vfs/LogFiles/Application/",
    headers=headers
)
```

**Key log files:**
- `*_default_docker.log` — Application stdout (gunicorn/uvicorn startup, MCP session manager start)
- `*_easyauth_docker.log` — Authentication middleware logs
- `*_docker.log` — Container startup/shutdown
- `Application/diagnostics-*.txt` — Detailed request-level logs (HTTP status codes, timings)

### Verify MCP endpoint works (with auth disabled temporarily)

```bash
# Temporarily disable auth
az rest --method PUT \
  --uri "https://management.azure.com/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.Web/sites/$APP/config/authsettingsV2?api-version=2024-04-01" \
  # ... set platform.enabled = false

# Test MCP
curl -X POST "https://$APP.azurewebsites.net/mcp/mcp" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'

# Re-enable auth after testing!
```

---

## 10. Test Cases

### Setup: Variables used across all tests

```bash
APP="saiyo-rjdd6dwlkawae-app-service"
APP_URL="https://$APP.azurewebsites.net"
MCP_URL="$APP_URL/mcp/mcp"
PRM_URL="$APP_URL/.well-known/oauth-protected-resource"
APP_CLIENT_ID="428b00cd-3f42-439a-bd47-15b287a6ef1e"
TENANT_ID="6907edd8-11e5-421c-8f84-a3c0bd847a11"
SUBSCRIPTION="1577b43a-6b5c-4c1d-845e-2e50d692189b"
RG="saiyo-rg"

# MCP initialize payload (reused in multiple tests)
MCP_INIT='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"test-client","version":"1.0"}}}'

# MCP tools/list payload
MCP_TOOLS='{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
```

---

### Test Group A: Without Authentication (auth disabled)

> Temporarily disable auth for these tests, then re-enable.

```bash
# Disable auth
az rest --method PUT \
  --uri "https://management.azure.com/subscriptions/$SUBSCRIPTION/resourceGroups/$RG/providers/Microsoft.Web/sites/$APP/config/authsettingsV2?api-version=2024-04-01" \
  --headers "Content-Type=application/json" \
  --body '{"properties":{"platform":{"enabled":false}}}'

sleep 10  # Wait for config to propagate
```

#### Test A1: Root endpoint returns 200 (no auth required)

```bash
STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$APP_URL/")
echo "Test A1 - Root without auth: $STATUS"
# EXPECTED: 200
```

#### Test A2: PRM endpoint not available when auth is off

```bash
STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$PRM_URL")
echo "Test A2 - PRM without auth: $STATUS"
# EXPECTED: 404 (PRM is served by EasyAuth, not the app)
```

#### Test A3: MCP initialize without auth

```bash
RESPONSE=$(curl -s -X POST "$MCP_URL" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d "$MCP_INIT")
echo "Test A3 - MCP init without auth:"
echo "$RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$RESPONSE"
# EXPECTED: JSON-RPC response with serverInfo, protocolVersion, capabilities
```

#### Test A4: MCP tools/list without auth

```bash
# First initialize to get a session (for stateful), or call directly (stateless)
RESPONSE=$(curl -s -X POST "$MCP_URL" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d "$MCP_TOOLS")
echo "Test A4 - MCP tools/list without auth:"
echo "$RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$RESPONSE"
# EXPECTED: JSON-RPC response with list of 4 tools
```

#### Test A5: MCP tool call — list_restaurants_mcp

```bash
CALL_PAYLOAD='{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"list_restaurants_mcp","arguments":{}}}'
RESPONSE=$(curl -s -X POST "$MCP_URL" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d "$CALL_PAYLOAD")
echo "Test A5 - Tool call without auth:"
echo "$RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$RESPONSE"
# EXPECTED: JSON-RPC response with restaurant data from database
```

#### Test A6: MCP tool call — get_details_mcp

```bash
CALL_PAYLOAD='{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"get_details_mcp","arguments":{"restaurant_id":1}}}'
RESPONSE=$(curl -s -X POST "$MCP_URL" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d "$CALL_PAYLOAD")
echo "Test A6 - get_details without auth:"
echo "$RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$RESPONSE"
# EXPECTED: JSON-RPC response with restaurant details and reviews
```

#### Test A7: MCP tool call — create_review_mcp

```bash
CALL_PAYLOAD='{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"create_review_mcp","arguments":{"restaurant_id":1,"user_name":"TestUser","rating":5,"review_text":"Great food!"}}}'
RESPONSE=$(curl -s -X POST "$MCP_URL" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d "$CALL_PAYLOAD")
echo "Test A7 - create_review without auth:"
echo "$RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$RESPONSE"
# EXPECTED: JSON-RPC response with created review object
```

#### Test A8: MCP tool call — create_restaurant_mcp

```bash
CALL_PAYLOAD='{"jsonrpc":"2.0","id":6,"method":"tools/call","params":{"name":"create_restaurant_mcp","arguments":{"restaurant_name":"Test Restaurant","street_address":"123 Test St","description":"A test restaurant"}}}'
RESPONSE=$(curl -s -X POST "$MCP_URL" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d "$CALL_PAYLOAD")
echo "Test A8 - create_restaurant without auth:"
echo "$RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$RESPONSE"
# EXPECTED: JSON-RPC response with created restaurant object
```

```bash
# RE-ENABLE AUTH after tests
az rest --method GET \
  --uri "https://management.azure.com/subscriptions/$SUBSCRIPTION/resourceGroups/$RG/providers/Microsoft.Web/sites/$APP/config/authsettingsV2?api-version=2024-04-01" \
  -o /tmp/auth_config.json

# Edit /tmp/auth_config.json to set platform.enabled = true, then:
az rest --method PUT \
  --uri "https://management.azure.com/subscriptions/$SUBSCRIPTION/resourceGroups/$RG/providers/Microsoft.Web/sites/$APP/config/authsettingsV2?api-version=2024-04-01" \
  --headers "Content-Type=application/json" \
  --body @/tmp/auth_config.json

sleep 10
```

---

### Test Group B: With Authentication (auth enabled)

#### Test B1: Unauthenticated request returns 401

```bash
STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$APP_URL/")
echo "Test B1 - Root without token: $STATUS"
# EXPECTED: 401
```

#### Test B2: PRM endpoint is accessible without auth

```bash
RESPONSE=$(curl -s "$PRM_URL")
echo "Test B2 - PRM endpoint:"
echo "$RESPONSE" | python3 -m json.tool
# EXPECTED: 200 with JSON containing resource, authorization_servers, scopes_supported
# {
#   "resource": "https://saiyo-rjdd6dwlkawae-app-service.azurewebsites.net",
#   "authorization_servers": ["https://login.microsoftonline.com/<TENANT>/v2.0"],
#   "scopes_supported": ["api://428b00cd-.../user_impersonation"]
# }
```

#### Test B3: MCP endpoint returns 401 without token

```bash
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$MCP_URL" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d "$MCP_INIT")
echo "Test B3 - MCP without token: $STATUS"
# EXPECTED: 401
```

#### Test B4: Request with invalid/expired token returns 401

```bash
FAKE_TOKEN="eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.fake"
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$MCP_URL" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Authorization: Bearer $FAKE_TOKEN" \
  -d "$MCP_INIT")
echo "Test B4 - MCP with fake token: $STATUS"
# EXPECTED: 401
```

#### Test B5: Token with wrong audience returns 401

```bash
# Get a token for a different resource (management API), not our app
WRONG_TOKEN=$(az account get-access-token --resource https://management.azure.com --query accessToken -o tsv)
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$MCP_URL" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Authorization: Bearer $WRONG_TOKEN" \
  -d "$MCP_INIT")
echo "Test B5 - MCP with wrong-audience token: $STATUS"
# EXPECTED: 401 (audience mismatch)
```

#### Test B6: Token with correct audience — MCP initialize succeeds

```bash
# Get a token for the correct audience
# Option 1: Using az cli (interactive user flow)
TOKEN=$(az account get-access-token \
  --resource "api://$APP_CLIENT_ID" \
  --query accessToken -o tsv)

# Option 2: Using client_credentials (app-to-app)
# TOKEN=$(curl -s -X POST "https://login.microsoftonline.com/$TENANT_ID/oauth2/v2/token" \
#   -d "client_id=<CLIENT_ID>&client_secret=<SECRET>&scope=api://$APP_CLIENT_ID/.default&grant_type=client_credentials" \
#   | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

RESPONSE=$(curl -s -X POST "$MCP_URL" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Authorization: Bearer $TOKEN" \
  -d "$MCP_INIT")
echo "Test B6 - MCP init with valid token:"
echo "$RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$RESPONSE"
# EXPECTED: JSON-RPC response with serverInfo, capabilities
```

#### Test B7: Authenticated MCP tools/list

```bash
RESPONSE=$(curl -s -X POST "$MCP_URL" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Authorization: Bearer $TOKEN" \
  -d "$MCP_TOOLS")
echo "Test B7 - tools/list with valid token:"
echo "$RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$RESPONSE"
# EXPECTED: JSON-RPC response listing 4 tools:
#   list_restaurants_mcp, get_details_mcp, create_review_mcp, create_restaurant_mcp
```

#### Test B8: Authenticated tool call — list_restaurants_mcp

```bash
CALL_PAYLOAD='{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"list_restaurants_mcp","arguments":{}}}'
RESPONSE=$(curl -s -X POST "$MCP_URL" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Authorization: Bearer $TOKEN" \
  -d "$CALL_PAYLOAD")
echo "Test B8 - list_restaurants with valid token:"
echo "$RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$RESPONSE"
# EXPECTED: JSON-RPC response with restaurant list from database
```

#### Test B9: Authenticated tool call — get_details_mcp

```bash
CALL_PAYLOAD='{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"get_details_mcp","arguments":{"restaurant_id":1}}}'
RESPONSE=$(curl -s -X POST "$MCP_URL" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Authorization: Bearer $TOKEN" \
  -d "$CALL_PAYLOAD")
echo "Test B9 - get_details with valid token:"
echo "$RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$RESPONSE"
# EXPECTED: JSON-RPC response with restaurant + reviews
```

#### Test B10: Authenticated tool call — create_review_mcp

```bash
CALL_PAYLOAD='{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"create_review_mcp","arguments":{"restaurant_id":1,"user_name":"AuthTestUser","rating":4,"review_text":"Tested with auth!"}}}'
RESPONSE=$(curl -s -X POST "$MCP_URL" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Authorization: Bearer $TOKEN" \
  -d "$CALL_PAYLOAD")
echo "Test B10 - create_review with valid token:"
echo "$RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$RESPONSE"
# EXPECTED: JSON-RPC response with the created review object
```

#### Test B11: Authenticated tool call — create_restaurant_mcp

```bash
CALL_PAYLOAD='{"jsonrpc":"2.0","id":6,"method":"tools/call","params":{"name":"create_restaurant_mcp","arguments":{"restaurant_name":"Auth Test Place","street_address":"456 Secure Blvd","description":"Created with authentication"}}}'
RESPONSE=$(curl -s -X POST "$MCP_URL" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Authorization: Bearer $TOKEN" \
  -d "$CALL_PAYLOAD")
echo "Test B11 - create_restaurant with valid token:"
echo "$RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$RESPONSE"
# EXPECTED: JSON-RPC response with the created restaurant object
```

---

### Test Group C: MCP Python Client (programmatic)

#### Test C1: Full MCP client test without auth

```python
"""Run with auth disabled. Requires: pip install mcp"""
import asyncio
from mcp.client.streamable_http import streamablehttp_client
from mcp import ClientSession

APP_URL = "https://saiyo-rjdd6dwlkawae-app-service.azurewebsites.net/mcp/mcp"

async def test_no_auth():
    async with streamablehttp_client(APP_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print(f"Server: {session.server_info}")

            tools = await session.list_tools()
            print(f"Tools ({len(tools.tools)}):")
            for t in tools.tools:
                print(f"  - {t.name}: {t.description}")

            # Call a tool
            result = await session.call_tool("list_restaurants_mcp", {})
            print(f"Restaurants: {result.content}")

asyncio.run(test_no_auth())
# EXPECTED: Prints server info, 4 tools, and restaurant data
```

#### Test C2: Full MCP client test with auth

```python
"""Run with auth enabled. Requires: pip install mcp azure-identity"""
import asyncio
import httpx
from mcp.client.streamable_http import streamablehttp_client
from mcp import ClientSession

APP_URL = "https://saiyo-rjdd6dwlkawae-app-service.azurewebsites.net/mcp/mcp"
APP_CLIENT_ID = "428b00cd-3f42-439a-bd47-15b287a6ef1e"
TENANT_ID = "6907edd8-11e5-421c-8f84-a3c0bd847a11"

async def get_token():
    """Get token using client credentials (replace with your client)."""
    from azure.identity.aio import ClientSecretCredential
    # For testing: use a client that has MCP.Access role assigned
    credential = ClientSecretCredential(
        tenant_id=TENANT_ID,
        client_id="<YOUR_TEST_CLIENT_ID>",
        client_secret="<YOUR_TEST_CLIENT_SECRET>"
    )
    token = await credential.get_token(f"api://{APP_CLIENT_ID}/.default")
    await credential.close()
    return token.token

async def test_with_auth():
    token = await get_token()
    headers = {"Authorization": f"Bearer {token}"}

    async with streamablehttp_client(APP_URL, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print(f"Server: {session.server_info}")

            tools = await session.list_tools()
            print(f"Tools ({len(tools.tools)}):")
            for t in tools.tools:
                print(f"  - {t.name}: {t.description}")

            # Call each tool
            result = await session.call_tool("list_restaurants_mcp", {})
            print(f"\nlist_restaurants: {result.content}")

            result = await session.call_tool("get_details_mcp", {"restaurant_id": 1})
            print(f"\nget_details: {result.content}")

            result = await session.call_tool("create_review_mcp", {
                "restaurant_id": 1,
                "user_name": "PythonTestUser",
                "rating": 5,
                "review_text": "Tested via MCP Python client with auth!"
            })
            print(f"\ncreate_review: {result.content}")

asyncio.run(test_with_auth())
# EXPECTED: Same output as C1 — server info, tools, restaurant data
```

#### Test C3: MCP client with auth — expect failure without token

```python
"""Run with auth enabled, but no token. Should fail."""
import asyncio
from mcp.client.streamable_http import streamablehttp_client
from mcp import ClientSession

APP_URL = "https://saiyo-rjdd6dwlkawae-app-service.azurewebsites.net/mcp/mcp"

async def test_no_token_with_auth_enabled():
    try:
        async with streamablehttp_client(APP_URL) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                print("ERROR: Should not have reached here!")
    except Exception as e:
        print(f"Expected error: {type(e).__name__}: {e}")
        # EXPECTED: HTTP 401 error or connection refused

asyncio.run(test_no_token_with_auth_enabled())
# EXPECTED: Exception indicating 401 Unauthorized
```

---

### Test Summary Table

| Test | Auth State | Token | Expected Result |
|------|-----------|-------|-----------------|
| A1 | Disabled | None | 200 OK |
| A2 | Disabled | None | 404 (PRM not served) |
| A3 | Disabled | None | MCP initialize succeeds |
| A4 | Disabled | None | tools/list returns 4 tools |
| A5 | Disabled | None | list_restaurants returns data |
| A6 | Disabled | None | get_details returns data |
| A7 | Disabled | None | create_review returns created object |
| A8 | Disabled | None | create_restaurant returns created object |
| B1 | Enabled | None | 401 Unauthorized |
| B2 | Enabled | None | 200 (PRM is exempt from auth) |
| B3 | Enabled | None | 401 Unauthorized |
| B4 | Enabled | Fake | 401 Unauthorized |
| B5 | Enabled | Wrong audience | 401 Unauthorized |
| B6 | Enabled | Valid | MCP initialize succeeds |
| B7 | Enabled | Valid | tools/list returns 4 tools |
| B8 | Enabled | Valid | list_restaurants returns data |
| B9 | Enabled | Valid | get_details returns data |
| B10 | Enabled | Valid | create_review returns created object |
| B11 | Enabled | Valid | create_restaurant returns created object |
| C1 | Disabled | None | Python client full flow succeeds |
| C2 | Enabled | Valid | Python client full flow succeeds |
| C3 | Enabled | None | Python client raises 401 error |

---

## Architecture Summary

```
Foundry Agent (mslearnagent)
    │
    │ 1. Gets token via client_credentials flow
    │    (audience: api://428b00cd-... or 428b00cd-...)
    │    (role: MCP.Access)
    │
    ▼
Azure App Service (EasyAuth ~2)
    │
    │ 2. Validates JWT:
    │    ✓ Issuer matches tenant
    │    ✓ Audience in allowedAudiences
    │    ✓ Client appId in allowedClientApplications
    │
    ▼
FastAPI + gunicorn (lifespan: on)
    │
    │ 3. Routes /mcp/mcp to FastMCP
    │
    ▼
FastMCP (stateless_http=True)
    │
    │ 4. Handles MCP JSON-RPC protocol
    │    (initialize, tools/list, tools/call)
    │
    ▼
MCP Tools (list_restaurants, get_details, etc.)
```

---

## Quick Reference: All Azure Configurations

### App Registration (`saiyo-mcp-server-auth`)
- **Client ID**: `428b00cd-3f42-439a-bd47-15b287a6ef1e`
- **Application ID URI**: `api://428b00cd-3f42-439a-bd47-15b287a6ef1e`
- **Delegated Scope**: `user_impersonation` (for interactive users)
- **App Role**: `MCP.Access` (for managed identities / service principals)
- **Service Principal**: `9d820733-4882-4aa9-94d2-4a1feb5e79b2`

### App Service Authentication
- **Runtime Version**: `~2`
- **Unauthenticated Action**: Return 401
- **Allowed Audiences**: `api://428b00cd-...`, `428b00cd-...`
- **Allowed Client Applications**: `2286091f-...` (agent), `d60af655-...` (project)

### App Settings
- `MICROSOFT_PROVIDER_AUTHENTICATION_SECRET` — Client secret for EasyAuth
- `WEBSITE_AUTH_PRM_DEFAULT_WITH_SCOPES` — `api://428b00cd-.../user_impersonation`
