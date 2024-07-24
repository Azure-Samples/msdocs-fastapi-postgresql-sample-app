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
urlFragment: msdocs-fastapi-postgresql-sample-app
name: Deploy FastAPI application with PostgreSQL on Azure App Service (Python)
description: This project deploys a restaurant review web application using FastAPI with Python and Azure Database for PostgreSQL - Flexible Server. It's set up for easy deployment with the Azure Developer CLI.
---
<!-- YAML front-matter schema: https://review.learn.microsoft.com/en-us/help/contribute/samples/process/onboarding?branch=main#supported-metadata-fields-for-readmemd -->

# Deploy FastAPI application with PostgreSQL via Azure App Service

This project deploys a web application for a restaurnant review site using FastAPI. The application can be deployed to Azure with Azure App Service using the [Azure Developer CLI](https://learn.microsoft.com/azure/developer/azure-developer-cli/overview).


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

## Getting help

If you're working with this project and running into issues, please post in [Issues](/issues).
