param name string
param location string = resourceGroup().location
param tags object = {}
param pythonVersion string
param appCommandLine string
param keyVaultName string
param applicationInsightsName string
param dbserverDomainName string
param dbserverUser string
param dbserverDatabaseName string

resource applicationInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: applicationInsightsName
}

module appServicePlan 'core/host/appserviceplan.bicep' = {
  name: 'serviceplan'
  params: {
    name: '${name}-serviceplan'
    location: location
    tags: tags
    sku: {
      name: 'B1'
    }
    reserved: true
  }
}

module web 'core/host/appservice.bicep' = {
  name: 'appservice'
  params: {
    name: '${name}-web'
    location: location
    tags: union(tags, {'azd-service-name': 'web'})
    appCommandLine: appCommandLine
    appServicePlanId: appServicePlan.outputs.id
    keyVaultName: keyVaultName
    runtimeName: 'python'
    runtimeVersion: pythonVersion
    scmDoBuildDuringDeployment: true
    ftpsState: 'Disabled'
    managedIdentity: true
    appSettings: {
      APPLICATIONINSIGHTS_CONNECTION_STRING: applicationInsights.properties.ConnectionString
      RUNNING_IN_PRODUCTION: 'true'
      POSTGRES_HOST: dbserverDomainName
      POSTGRES_USERNAME: dbserverUser
      POSTGRES_DATABASE: dbserverDatabaseName
      POSTGRES_PASSWORD: '@Microsoft.KeyVault(VaultName=${keyVaultName};SecretName=DBSERVERPASSWORD)'
      POSTGRES_SSL: 'require'
    }
  }
}

// Give the app access to KeyVault
module webKeyVaultAccess './core/security/keyvault-access.bicep' = {
  name: 'web-keyvault-access'
  params: {
    keyVaultName: keyVaultName
    principalId: web.outputs.identityPrincipalId
  }
}

output SERVICE_WEB_IDENTITY_PRINCIPAL_ID string = web.outputs.identityPrincipalId

output uri string = web.outputs.uri
