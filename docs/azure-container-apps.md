# Deploy to Azure Container Apps

This project is a two-service app:

- `backend`: FastAPI API, listens on port `8000`
- `frontend`: vinext/Node app, listens on port `3000`

For a short-lived demo, use Azure Container Apps for both services and keep the backend data on the container filesystem. This is intentionally non-persistent: uploaded files, edits, validation state, and audit logs can be lost on restart, redeploy, or reschedule.

For a more durable prototype, add Azure Files for the backend's SQLite database and uploaded files. For production, replace SQLite/local files with Azure Database for PostgreSQL and Azure Blob Storage.

## Prerequisites

- Azure CLI installed
- Access to an Azure subscription
- Azure OpenAI resource details for extraction

```bash
az login
az extension add --name containerapp --upgrade
az provider register --namespace Microsoft.App
az provider register --namespace Microsoft.OperationalInsights
```

## 1. Set deployment variables

The ACR repository paths used by this app are:

- `contract-lifecycle/backend`
- `contract-lifecycle/frontend`

```bash
APP_PREFIX=clausecontract
LOCATION=southeastasia
RESOURCE_GROUP=
CONTAINER_ENV=${APP_PREFIX}-env
ACR_NAME=
BACKEND_APP=${APP_PREFIX}-api
FRONTEND_APP=${APP_PREFIX}-web
BACKEND_IMAGE_REPO=contract-lifecycle/backend
FRONTEND_IMAGE_REPO=contract-lifecycle/frontend
IMAGE_TAG=$(date +%Y%m%d%H%M%S)

# Replace this with your office/VPN/public egress IP range.
# Use /32 for one exact IPv4 address
ALLOWED_CLIENT_CIDR=
```

Set your Azure OpenAI values locally before creating the backend app:

```bash
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
AZURE_OPENAI_API_KEY=replace-with-your-key
AZURE_OPENAI_DEPLOYMENT=gpt-5.4
AZURE_OPENAI_API_VERSION=2025-04-01-preview
```

## 2. Create the Container Apps environment

```bash
ACR_LOGIN_SERVER=$(az acr show \
  --name $ACR_NAME \
  --resource-group $RESOURCE_GROUP \
  --query loginServer \
  --output tsv)

ACR_USERNAME=$(az acr credential show \
  --name $ACR_NAME \
  --query username \
  --output tsv)

ACR_PASSWORD=$(az acr credential show \
  --name $ACR_NAME \
  --query "passwords[0].value" \
  --output tsv)

az containerapp env create \
  --name $CONTAINER_ENV \
  --resource-group $RESOURCE_GROUP \
  --location $LOCATION
```

If the Container Apps environment already exists, skip the `az containerapp env create` command and set `CONTAINER_ENV` to the existing environment name.

## 3. Demo storage mode

For the demo, skip Azure Files. The backend container writes SQLite and uploads to `/data` inside the container filesystem.

To minimize idle cost, scale both apps to zero when idle:

```bash
--min-replicas 0 \
--max-replicas 1
```

Azure Container Apps allows `min-replicas 0`, but `max-replicas` must be at least `1`. This keeps the demo low-cost, but cold starts are expected after idle periods.

The backend data is not durable. Expect data loss when the backend app restarts, scales to zero, is rescheduled, or is redeployed.

## 4. Build and deploy the backend

Build the backend image from the repository root:

```bash
az acr build \
  --registry $ACR_NAME \
  --image $BACKEND_IMAGE_REPO:$IMAGE_TAG \
  --file backend/Dockerfile \
  .

BACKEND_IMAGE=$ACR_LOGIN_SERVER/$BACKEND_IMAGE_REPO:$IMAGE_TAG
```

Create the backend Container App:

```bash
az containerapp create \
  --name $BACKEND_APP \
  --resource-group $RESOURCE_GROUP \
  --environment $CONTAINER_ENV \
  --image $BACKEND_IMAGE \
  --registry-server $ACR_LOGIN_SERVER \
  --registry-username $ACR_USERNAME \
  --registry-password $ACR_PASSWORD \
  --target-port 8000 \
  --ingress internal \
  --min-replicas 0 \
  --max-replicas 1 \
  --secrets \
    azure-openai-endpoint="$AZURE_OPENAI_ENDPOINT" \
    azure-openai-api-key="$AZURE_OPENAI_API_KEY" \
    clause-seed-password="$CLAUSE_SEED_PASSWORD" \
  --env-vars \
    PORT=8000 \
    DATABASE_PATH=/data/contracts.db \
    UPLOAD_DIR=/data/uploads \
    AZURE_OPENAI_ENDPOINT=secretref:azure-openai-endpoint \
    AZURE_OPENAI_API_KEY=secretref:azure-openai-api-key \
    AZURE_OPENAI_DEPLOYMENT=$AZURE_OPENAI_DEPLOYMENT \
    AZURE_OPENAI_API_VERSION=$AZURE_OPENAI_API_VERSION \
    CLAUSE_SEED_PASSWORD=secretref:clause-seed-password \
    COOKIE_SECURE=1 \
    PUBLIC_APP_URL="https://<frontend-app-fqdn>" \
    SEED_DEMO_DATA=0
```

Set `PUBLIC_APP_URL` to the frontend's public address: e-signature links and the single sign-on redirect point there.

To use Entra ID sign-in, add the client secret as a secret and the SSO variables from the README:

```bash
az containerapp secret set --name "$BACKEND_APP" --resource-group "$RESOURCE_GROUP" \
  --secrets sso-client-secret="$SSO_CLIENT_SECRET"
az containerapp update --name "$BACKEND_APP" --resource-group "$RESOURCE_GROUP" \
  --set-env-vars ENTRA_TENANT_ID="$ENTRA_TENANT_ID" SSO_CLIENT_ID="$SSO_CLIENT_ID" \
    SSO_CLIENT_SECRET=secretref:sso-client-secret PASSWORD_LOGIN=0
```

Set `CLAUSE_SEED_PASSWORD` to a strong value before the first start. It becomes the initial password of the seeded accounts (see the README), so sign in and change each one, or deactivate the ones you do not need. `COOKIE_SECURE=1` restricts the session cookie to HTTPS. Keep `--max-replicas 1` while you use SQLite. With PostgreSQL (`DATABASE_URL`, see the README) you can run several replicas: reminders are de-duplicated in the database, so each is sent once. Mount the same Azure Files share for `UPLOAD_DIR` on every replica.

The backend is internal-only. Other Container Apps in the same environment can call it by app name:

```bash
BACKEND_API_URL=http://$BACKEND_APP
```

## 5. Build and deploy the frontend

The browser calls the frontend on same-origin `/backend-api/...`. The frontend server proxies those requests to `BACKEND_API_URL`, so the internal backend is never exposed directly to the public internet.

```bash
az acr build \
  --registry $ACR_NAME \
  --image $FRONTEND_IMAGE_REPO:$IMAGE_TAG \
  --file frontend/Dockerfile \
  .

FRONTEND_IMAGE=$ACR_LOGIN_SERVER/$FRONTEND_IMAGE_REPO:$IMAGE_TAG
```

Deploy the frontend:

```bash
az containerapp create \
  --name $FRONTEND_APP \
  --resource-group $RESOURCE_GROUP \
  --environment $CONTAINER_ENV \
  --image $FRONTEND_IMAGE \
  --registry-server $ACR_LOGIN_SERVER \
  --registry-username $ACR_USERNAME \
  --registry-password $ACR_PASSWORD \
  --target-port 3000 \
  --ingress external \
  --min-replicas 0 \
  --max-replicas 1 \
  --env-vars \
    PORT=3000 \
    BACKEND_API_URL=$BACKEND_API_URL
```

Capture the frontend URL:

```bash
FRONTEND_FQDN=$(az containerapp show \
  --name $FRONTEND_APP \
  --resource-group $RESOURCE_GROUP \
  --query properties.configuration.ingress.fqdn \
  --output tsv)

FRONTEND_URL=https://$FRONTEND_FQDN

echo $FRONTEND_URL
```

## 6. Restrict frontend access by IP address

Container Apps IP restrictions use CIDR notation. Use `/32` for one exact IPv4 address. Add one allow rule per office/VPN/public egress range.

```bash
az containerapp ingress access-restriction set \
  --name $FRONTEND_APP \
  --resource-group $RESOURCE_GROUP \
  --rule-name allow-primary-client-range \
  --description "Allow approved demo client IP range" \
  --ip-address $ALLOWED_CLIENT_CIDR \
  --action Allow
```

List configured restrictions:

```bash
az containerapp ingress access-restriction list \
  --name $FRONTEND_APP \
  --resource-group $RESOURCE_GROUP
```

Then open the frontend from an allowed network:

```bash
open $FRONTEND_URL
```

## 7. Updating after code changes

Rebuild and update the changed service image.

Backend:

```bash
IMAGE_TAG=$(date +%Y%m%d%H%M%S)

az acr build \
  --registry $ACR_NAME \
  --image $BACKEND_IMAGE_REPO:$IMAGE_TAG \
  --file backend/Dockerfile \
  .

az containerapp update \
  --name $BACKEND_APP \
  --resource-group $RESOURCE_GROUP \
  --image $ACR_LOGIN_SERVER/$BACKEND_IMAGE_REPO:$IMAGE_TAG
```

Frontend:

```bash
IMAGE_TAG=$(date +%Y%m%d%H%M%S)

az acr build \
  --registry $ACR_NAME \
  --image $FRONTEND_IMAGE_REPO:$IMAGE_TAG \
  --file frontend/Dockerfile \
  .

az containerapp update \
  --name $FRONTEND_APP \
  --resource-group $RESOURCE_GROUP \
  --image $ACR_LOGIN_SERVER/$FRONTEND_IMAGE_REPO:$IMAGE_TAG
```

## 8. Logs

```bash
az containerapp logs show \
  --name $BACKEND_APP \
  --resource-group $RESOURCE_GROUP \
  --follow

az containerapp logs show \
  --name $FRONTEND_APP \
  --resource-group $RESOURCE_GROUP \
  --follow
```

## 9. Optional: add Azure Files later

Use this when you want the demo data to survive backend restarts and redeployments while still keeping SQLite.

```bash
STORAGE_ACCOUNT=${APP_PREFIX}st$RANDOM
STORAGE_SHARE=contractdata
STORAGE_MOUNT=contractdata

az storage account create \
  --resource-group $RESOURCE_GROUP \
  --name $STORAGE_ACCOUNT \
  --location $LOCATION \
  --kind StorageV2 \
  --sku Standard_LRS \
  --enable-large-file-share

az storage share-rm create \
  --resource-group $RESOURCE_GROUP \
  --storage-account $STORAGE_ACCOUNT \
  --name $STORAGE_SHARE \
  --quota 100 \
  --enabled-protocols SMB

STORAGE_KEY=$(az storage account keys list \
  --resource-group $RESOURCE_GROUP \
  --account-name $STORAGE_ACCOUNT \
  --query "[0].value" \
  --output tsv)

az containerapp env storage set \
  --name $CONTAINER_ENV \
  --resource-group $RESOURCE_GROUP \
  --storage-name $STORAGE_MOUNT \
  --storage-type AzureFile \
  --azure-file-account-name $STORAGE_ACCOUNT \
  --azure-file-account-key $STORAGE_KEY \
  --azure-file-share-name $STORAGE_SHARE \
  --access-mode ReadWrite
```

Then mount that storage definition into the backend Container App at `/data` using a new revision in the Azure portal, ARM/Bicep, or a Container Apps YAML update.

Portal path:

1. Open the backend Container App.
2. Go to **Revisions and replicas**.
3. Create a new revision.
4. Add an Azure Files volume using the storage definition named `$STORAGE_MOUNT`.
5. Mount it into the backend container at `/data`.

## Notes

- Do not commit real Azure OpenAI keys.
- Keep the backend Container App on internal ingress. Browser requests should go through frontend `/backend-api`, not directly to the backend.
- Keep `BACKEND_API_URL=http://$BACKEND_APP` on the frontend app when both apps are in the same Container Apps environment.
- Use allow-list IP restrictions on the frontend app. A single IP must be written as CIDR, for example `203.0.113.10/32`.
- `min-replicas 0` reduces idle cost but causes cold starts. `max-replicas 0` is not valid for Container Apps; use `max-replicas 1` for this demo.
- For production, disable ACR admin credentials and use managed identity-based image pulls.
- Demo mode without Azure Files is non-persistent. Use it only when data loss is acceptable.
- For production, migrate from SQLite/Azure Files to PostgreSQL and Blob Storage before multiple users rely on the app.
