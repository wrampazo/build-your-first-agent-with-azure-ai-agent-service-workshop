#!/bin/bash

echo "Deploying the Azure resources..."

# Define resource group parameters
RG_LOCATION="westus"
MODEL_NAME="gpt-4o-mini"
MODEL_VERSION="2024-07-18"
AI_PROJECT_FRIENDLY_NAME="Contoso Agent Service Workshop"
MODEL_CAPACITY=120

# Generate a unique suffix (4 character random string)
UNIQUE_SUFFIX=$(printf "%04d" $((RANDOM % 10000)))
DEPLOYMENT_NAME="azure-ai-agent-service-lab-${UNIQUE_SUFFIX}"

# Print the resource group name that will be created
RESOURCE_GROUP_NAME="rg-contoso-agent-workshop-${UNIQUE_SUFFIX}"
echo "Resource group that will be created: $RESOURCE_GROUP_NAME"

# Deploy the Azure resources and save output to JSON
az deployment sub create \
  --name "$DEPLOYMENT_NAME" \
  --location "$RG_LOCATION" \
  --template-file main.bicep \
  --parameters \
      uniqueSuffix="$UNIQUE_SUFFIX" \
      resourcePrefix="contoso-agent-workshop" \
      location="$RG_LOCATION" \
      aiProjectFriendlyName="$AI_PROJECT_FRIENDLY_NAME" \
      modelName="$MODEL_NAME" \
      modelCapacity="$MODEL_CAPACITY" \
      modelVersion="$MODEL_VERSION" > output.json

# Parse the JSON file manually using grep and sed
if [ ! -f output.json ]; then
  echo "Error: output.json not found."
  exit -1
fi

PROJECTS_ENDPOINT=$(jq -r '.properties.outputs.projectsEndpoint.value' output.json)
RESOURCE_GROUP_NAME=$(jq -r '.properties.outputs.resourceGroupName.value' output.json)
SUBSCRIPTION_ID=$(jq -r '.properties.outputs.subscriptionId.value' output.json)
AI_SERVICE_NAME=$(jq -r '.properties.outputs.aiAccountName.value' output.json)
AI_PROJECT_NAME=$(jq -r '.properties.outputs.aiProjectName.value' output.json)

if [ -z "$PROJECTS_ENDPOINT" ]; then
  echo "Error: projectsEndpoint not found. Possible deployment failure."
  exit -1
fi

ENV_FILE_PATH="../src/python/workshop/.env"

# Delete the file if it exists
[ -f "$ENV_FILE_PATH" ] && rm "$ENV_FILE_PATH"


# Write to the .env file
{
  echo "PROJECT_ENDPOINT=$PROJECTS_ENDPOINT"
  echo "MODEL_DEPLOYMENT_NAME=\"$MODEL_NAME\""
} > "$ENV_FILE_PATH"

CSHARP_PROJECT_PATH="../src/csharp/workshop/AgentWorkshop.Client/AgentWorkshop.Client.csproj"

# Set the user secrets for the C# project
dotnet user-secrets set "ConnectionStrings:AiAgentService" "$PROJECTS_ENDPOINT" --project "$CSHARP_PROJECT_PATH"
dotnet user-secrets set "Azure:ModelName" "$MODEL_NAME" --project "$CSHARP_PROJECT_PATH"

# Delete the output.json file
rm -f output.json

echo "Adding Azure AI Developer user role"

# Set Variables
subId=$(az account show --query id --output tsv)
objectId=$(az ad signed-in-user show --query id -o tsv)

az role assignment create --role "Azure AI Developer" \
                          --assignee-object-id "$objectId" \
                          --scope "subscriptions/$subId/resourceGroups/$RESOURCE_GROUP_NAME" \
                          --assignee-principal-type 'User'

# Check if the command failed
if [ $? -ne 0 ]; then
    echo "User role assignment failed."
    exit 1
fi

echo "User role assignment succeeded."