---
page_type: sample
languages:
- azdeveloper
- python
- bicep
- html
- css
- scss
products:
- azure
- azure-app-service
- azure-postgresql
- azure-virtual-network
- ai-services
urlFragment: msdocs-fastapi-postgresql-sample-app
name: Deploy FastAPI Application with PostgreSQL & MCP Server on Azure App Service with Entra Agent Identity Auth (Python)
description: This project deploys a restaurant review web application using FastAPI with Python, Azure Database for PostgreSQL - Flexible Server, and a Model Context Protocol (MCP) server secured with Microsoft Entra ID authentication. It demonstrates how to expose MCP tools to Azure AI Foundry agents using managed identity (agent identity) authentication.
---
<!-- YAML front-matter schema: https://review.learn.microsoft.com/en-us/help/contribute/samples/process/onboarding?branch=main#supported-metadata-fields-for-readmemd -->

# Deploy FastAPI Application with PostgreSQL & MCP Server on Azure App Service with Entra Agent Identity Auth

This project deploys a web application for a restaurant review site using **FastAPI**. It includes a **Model Context Protocol (MCP)** server that exposes restaurant review tools, secured with **Microsoft Entra ID** authentication and preauthorized for **Azure AI Foundry** agent identities.

The application can be deployed to Azure with Azure App Service using the [Azure Developer CLI](https://learn.microsoft.com/azure/developer/azure-developer-cli/overview).

### Key Features

- **FastAPI web app** — Restaurant review CRUD with PostgreSQL backend
- **MCP server** — 4 tools exposed via the [Model Context Protocol](https://modelcontextprotocol.io/) (`/mcp/mcp` endpoint)
- **Entra ID authentication** — EasyAuth v2 with Return401, Protected Resource Metadata (PRM)
- **Azure AI Foundry integration** — Agent identities preauthorized via app role assignments (`MCP.Access`)

### MCP Tools

| Tool | Description |
|------|-------------|
| `list_restaurants_mcp` | List all restaurants with average rating and review count |
| `get_details_mcp` | Get a restaurant's details and all its reviews |
| `create_review_mcp` | Add a new review to a restaurant |
| `create_restaurant_mcp` | Create a new restaurant |

### Architecture

```
Azure AI Foundry Agent
    │
    │  client_credentials flow (MCP.Access app role)
    ▼
Azure App Service (EasyAuth ~2, Return401)
    │
    │  JWT validated: issuer, audience, allowedClientApplications
    ▼
FastAPI + gunicorn (lifespan: on)
    │
    │  /mcp/mcp → FastMCP (stateless_http)
    ▼
MCP Tools → PostgreSQL
```

---

## Run the sample

This project has a [dev container configuration](.devcontainer/), which makes it easier to develop apps locally, deploy them to Azure, and monitor them. The easiest way to run this sample application is inside a GitHub codespace. Follow these steps:

1. Fork this repository to your account. For instructions, see [Fork a repo](https://docs.github.com/get-started/quickstart/fork-a-repo).

1. From the repository root of your fork, select **Code** > **Codespaces** > **+**.

1. In the codespace terminal, run the following commands:

    ```shell
    # Create .env with environment variables
    cp .env.sample.devcontainer .env

    # Install requirements
    python3 -m pip install -r src/requirements.txt

    # Install the app as an editable package
    python3 -m pip install -e src

    # Run database migrations
    python3 src/fastapi_app/seed_data.py

    # Start the development server
    python3 -m uvicorn fastapi_app:app --reload --port=8000
    ```

1. When you see the message `Your application running on port 8000 is available.`, click **Open in Browser**.

### Verify MCP server locally

Once the app is running, test the MCP endpoint:

```shell
curl -X POST http://localhost:8000/mcp/mcp \
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

## Running locally

If you're running the app inside VS Code or GitHub Codespaces, you can use the "Run and Debug" button to start the app.

```sh
python3 -m uvicorn fastapi_app:app --reload --port=8000
```

## Deployment

This repo is set up for deployment on Azure via Azure App Service.

Steps for deployment:

1. Sign up for a [free Azure account](https://azure.microsoft.com/free/) and create an Azure Subscription.
2. Install the [Azure Developer CLI](https://learn.microsoft.com/azure/developer/azure-developer-cli/install-azd). (If you open this repository in Codespaces or with the VS Code Dev Containers extension, that part will be done for you.)
3. Login to Azure:

    ```shell
    azd auth login
    ```

4. Provision and deploy all the resources:

    ```shell
    azd up
    ```

    It will prompt you to provide an `azd` environment name (like "myapp"), select a subscription from your Azure account, and select a location (like "eastus"). Then it will provision the resources in your account and deploy the latest code. If you get an error with deployment, changing the location can help, as there may be availability constraints for some of the resources.

5. When `azd` has finished deploying, you'll see an endpoint URI in the command output. Visit that URI, and you should see the front page of the app! 🎉

6. When you've made any changes to the app code, you can just run:

    ```shell
    azd deploy
    ```

## Secure with Entra ID and Connect Azure AI Foundry Agent

After deploying to Azure, follow these steps to secure the MCP endpoint with Entra ID authentication and authorize an Azure AI Foundry agent to call the MCP tools using its managed identity.

> For the complete step-by-step guide with all commands and troubleshooting, see [GUIDE_AUTH_MCP_SERVER.md](GUIDE_AUTH_MCP_SERVER.md).

### Step 1: Create an Entra ID App Registration

```shell
az ad app create --display-name "<your-app-name>-auth" --sign-in-audience AzureADMyOrg
```

- Set an Application ID URI: `api://<client-id>`
- Add a delegated scope: `user_impersonation`
- Add an application role: `MCP.Access` (allowedMemberTypes: `Application`) — required for agent identity auth
- Create a client secret
- **Create a service principal** (often missed):
  ```shell
  az ad sp create --id <client-id>
  ```

### Step 2: Enable App Service Authentication (EasyAuth)

1. Store the client secret as an app setting:
   ```shell
   az webapp config appsettings set --name <app> --resource-group <rg> \
     --settings MICROSOFT_PROVIDER_AUTHENTICATION_SECRET="<secret>"
   ```

2. Configure EasyAuth v2 via the ARM API with:
   - `runtimeVersion: "~2"` (must be v2 for proper enforcement)
   - `unauthenticatedClientAction: "Return401"`
   - `allowedAudiences`: both `api://<client-id>` and `<client-id>`
   - `allowedClientApplications`: your Foundry agent and project identity IDs

3. Restart the app service after changing auth config.

### Step 3: Enable Protected Resource Metadata (PRM)

```shell
az webapp config appsettings set --name <app> --resource-group <rg> \
  --settings WEBSITE_AUTH_PRM_DEFAULT_WITH_SCOPES="api://<client-id>/user_impersonation"
```

This makes `/.well-known/oauth-protected-resource` available, telling MCP clients how to authenticate.

### Step 4: Preauthorize Foundry Agent Identities

Azure AI Foundry agents use **managed identities** (`ServiceIdentity` type) that authenticate via **client_credentials** flow. They cannot be added to `preAuthorizedApplications` — instead:

1. **Grant app role assignments** to each Foundry identity (agent + project):
   ```shell
   az rest --method POST \
     --uri "https://graph.microsoft.com/v1.0/servicePrincipals/<agent-principal-id>/appRoleAssignments" \
     --headers "Content-Type=application/json" \
     --body '{
       "principalId": "<agent-principal-id>",
       "resourceId": "<your-service-principal-id>",
       "appRoleId": "<MCP.Access-role-id>"
     }'
   ```

2. **Add their IDs to `allowedClientApplications`** in the EasyAuth config.

### Step 5: Verify in Azure AI Foundry

1. Create or update an agent in Azure AI Foundry.
2. Add an MCP Server tool pointing to `https://<app>.azurewebsites.net/mcp/mcp`.
3. Run the agent — the `mcp_list_tools` trace span should show `status: OK` with all 4 tools enumerated.

### Key Learnings

- **`runtimeVersion: "~2"`** is required — v1 doesn't enforce auth properly.
- **A service principal must exist** for the app registration before role assignments work.
- **`ServiceIdentity` principals** (Foundry agents) can't use `preAuthorizedApplications`; use app role assignments + `allowedClientApplications` instead.
- **`lifespan: "on"`** in the gunicorn worker config is critical — without it, the MCP session manager won't start in production.
- The PRM endpoint (`/.well-known/oauth-protected-resource`) is served by EasyAuth and is **exempt from authentication** by design.

## Getting help

If you're working with this project and running into issues, please post in [Issues](/issues).
