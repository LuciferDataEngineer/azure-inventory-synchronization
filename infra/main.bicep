targetScope = 'resourceGroup'

@allowed(['dev'])
param deploymentStage string
param location string
param sqlAdminGroupName string
param sqlAdminGroupObjectId string
@description('Immutable public registry references for the three local Dockerfiles')
param functionImage string
param dashboardImage string
param mutationImage string
@description('One IPv4 address of approved operator; dashboard ingress and initial SQL administration.')
param operatorIp string

var suffix = uniqueString(resourceGroup().id)
var tags = { project: 'inventory-synchronization', stage: deploymentStage }
var reader = '00000000-0000-0000-0000-000000000001'
var contributor = '00000000-0000-0000-0000-000000000002'

resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' = {
  name: 'inv-cosmos-${suffix}'
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    locations: [{locationName: location, failoverPriority: 0, isZoneRedundant: false}]
    consistencyPolicy: {defaultConsistencyLevel: 'Session'}
    capabilities: [{name: 'EnableServerless'}]
    disableLocalAuth: true
    minimalTlsVersion: 'Tls12'
    disableKeyBasedMetadataWriteAccess: true
  }
}
resource database 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-05-15' = {
  parent: cosmos
  name: 'inventory'
  properties: {resource: {id: 'inventory'}}
}
resource containers 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = [for entry in [
  {name: 'products', path: '/tenant_id'}
  {name: 'leases', path: '/id'}
]: {
  parent: database
  name: entry.name
  properties: {
    resource: {
      id: entry.name
      partitionKey: {paths: [entry.path], kind: 'Hash', version: 2}
    }
  }
}]

resource network 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: 'inventory-vnet'
  location: location
  properties: {addressSpace: {addressPrefixes: ['10.63.0.0/16']}}
}
resource subnet 'Microsoft.Network/virtualNetworks/subnets@2024-05-01' = {
  parent: network
  name: 'apps'
  properties: {
    addressPrefix: '10.63.1.0/26'
    delegations: [{name: 'apps', properties: {serviceName: 'Microsoft.Web/serverFarms'}}]
    serviceEndpoints: [{service: 'Microsoft.Sql'}]
  }
}
resource sql 'Microsoft.Sql/servers@2023-08-01-preview' = {
  name: 'inv-sql-${suffix}'
  location: location
  tags: tags
  properties: {
    version: '12.0'
    minimalTlsVersion: '1.2'
    publicNetworkAccess: 'Enabled'
    administrators: {
      administratorType: 'ActiveDirectory'
      principalType: 'Group'
      login: sqlAdminGroupName
      sid: sqlAdminGroupObjectId
      tenantId: tenant().tenantId
      azureADOnlyAuthentication: true
    }
  }
}
resource sqlDatabase 'Microsoft.Sql/servers/databases@2023-08-01-preview' = {
  parent: sql
  name: 'Inventory'
  location: location
  sku: {name: 'Basic', tier: 'Basic', capacity: 5}
}
resource sqlVnet 'Microsoft.Sql/servers/virtualNetworkRules@2023-08-01-preview' = {
  parent: sql
  name: 'apps'
  properties: {virtualNetworkSubnetId: subnet.id, ignoreMissingVnetServiceEndpoint: false}
}
resource sqlAdminFirewall 'Microsoft.Sql/servers/firewallRules@2023-08-01-preview' = {
  parent: sql
  name: 'approved-operator'
  properties: {startIpAddress: operatorIp, endIpAddress: operatorIp}
}

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'inv-logs-${suffix}'
  location: location
  properties: {sku: {name: 'PerGB2018'}, retentionInDays: 30, workspaceCapping: {dailyQuotaGb: 1}}
}
resource insights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'inv-insights-${suffix}'
  location: location
  kind: 'web'
  properties: {Application_Type: 'web', WorkspaceResourceId: logs.id}
}
resource hostStore 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: 'invhost${suffix}'
  kind: 'StorageV2'
  location: location
  sku: {name: 'Standard_LRS'}
  properties: {minimumTlsVersion: 'TLS1_2', allowBlobPublicAccess: false, allowSharedKeyAccess: false, supportsHttpsTrafficOnly: true}
}
resource evidence 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: 'invevid${suffix}'
  kind: 'StorageV2'
  location: location
  sku: {name: 'Standard_LRS'}
  properties: {minimumTlsVersion: 'TLS1_2', allowBlobPublicAccess: false, allowSharedKeyAccess: false, supportsHttpsTrafficOnly: true}
}
resource evidenceBlobs 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: evidence
  name: 'default'
  properties: {deleteRetentionPolicy: {enabled: true, days: 7}}
}
resource quarantine 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: evidenceBlobs
  name: 'quarantine'
  properties: {publicAccess: 'None'}
}

resource plan 'Microsoft.Web/serverfarms@2024-04-01' = {
  name: 'inv-apps-${suffix}'
  location: location
  kind: 'linux'
  sku: {name: 'B1', tier: 'Basic', capacity: 1}
  properties: {reserved: true}
}
resource fn 'Microsoft.Web/sites@2024-04-01' = {
  name: 'inv-project-${suffix}'
  location: location
  kind: 'functionapp,linux,container'
  identity: {type: 'SystemAssigned'}
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    virtualNetworkSubnetId: subnet.id
    siteConfig: {
      alwaysOn: true
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      linuxFxVersion: 'DOCKER|${functionImage}'
      ipSecurityRestrictions: [{name: 'operator', ipAddress: '${operatorIp}/32', priority: 100, action: 'Allow'}]
      ipSecurityRestrictionsDefaultAction: 'Deny'
      scmIpSecurityRestrictionsUseMain: true
      appSettings: [
        {name: 'WEBSITES_ENABLE_APP_SERVICE_STORAGE', value: 'false'}
        {name: 'FUNCTIONS_EXTENSION_VERSION', value: '~4'}
        {name: 'FUNCTIONS_WORKER_RUNTIME', value: 'python'}
        {name: 'AzureWebJobsStorage__accountName', value: hostStore.name}
        {name: 'AzureWebJobsStorage__credential', value: 'managedidentity'}
        {name: 'InventoryCosmos__accountEndpoint', value: cosmos.properties.documentEndpoint}
        {name: 'InventoryCosmos__credential', value: 'managedidentity'}
        {name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: insights.properties.ConnectionString}
        {name: 'SQL_SERVER', value: sql.properties.fullyQualifiedDomainName}
        {name: 'QUARANTINE_ENDPOINT', value: evidence.properties.primaryEndpoints.blob}
      ]
    }
  }
}
resource web 'Microsoft.Web/sites@2024-04-01' = {
  name: 'inv-view-${suffix}'
  location: location
  kind: 'app,linux,container'
  identity: {type: 'SystemAssigned'}
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    virtualNetworkSubnetId: subnet.id
    siteConfig: {
      alwaysOn: true
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      linuxFxVersion: 'DOCKER|${dashboardImage}'
      ipSecurityRestrictions: [{name: 'operator', ipAddress: '${operatorIp}/32', priority: 100, action: 'Allow'}]
      ipSecurityRestrictionsDefaultAction: 'Deny'
      scmIpSecurityRestrictionsUseMain: true
      appSettings: [
        {name: 'WEBSITES_PORT', value: '8000'}
        {name: 'SQL_SERVER', value: sql.properties.fullyQualifiedDomainName}
      ]
    }
  }
}

resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'inv-jobs-${suffix}'
  location: location
  properties: {appLogsConfiguration: {destination: 'azure-monitor'}}
}
resource job 'Microsoft.App/jobs@2024-03-01' = {
  name: 'inv-mutate-${suffix}'
  location: location
  identity: {type: 'SystemAssigned'}
  properties: {
    environmentId: env.id
    configuration: {triggerType: 'Manual', replicaTimeout: 10800, replicaRetryLimit: 0, manualTriggerConfig: {parallelism: 1, replicaCompletionCount: 1}}
    template: {
      containers: [{
        name: 'produce'
        image: mutationImage
        resources: {cpu: json('0.25'), memory: '0.5Gi'}
        env: [
          {name: 'COSMOS_ENDPOINT', value: cosmos.properties.documentEndpoint}
          {name: 'MUTATION_DELAY_MS', value: '100'}
        ]
      }]
    }
  }
}
resource jobLogs 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'job-logs'
  scope: env
  properties: {workspaceId: logs.id, logs: [{category: 'ContainerAppConsoleLogs', enabled: true}, {category: 'ContainerAppSystemLogs', enabled: true}]}
}

resource fnRead 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = {
  parent: cosmos
  name: guid(cosmos.id, fn.id, 'read-products')
  properties: {
    scope: '${cosmos.id}/dbs/inventory/colls/products'
    roleDefinitionId: '${cosmos.id}/sqlRoleDefinitions/${reader}'
    principalId: fn.identity.principalId
  }
  dependsOn: [containers]
}
resource fnLeases 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = {
  parent: cosmos
  name: guid(cosmos.id, fn.id, 'write-leases')
  properties: {
    scope: '${cosmos.id}/dbs/inventory/colls/leases'
    roleDefinitionId: '${cosmos.id}/sqlRoleDefinitions/${contributor}'
    principalId: fn.identity.principalId
  }
  dependsOn: [containers]
}
resource jobWrite 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = {
  parent: cosmos
  name: guid(cosmos.id, job.id, 'mutate-products')
  properties: {
    scope: '${cosmos.id}/dbs/inventory/colls/products'
    roleDefinitionId: '${cosmos.id}/sqlRoleDefinitions/${contributor}'
    principalId: job.identity.principalId
  }
  dependsOn: [containers]
}
resource hostRights 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(hostStore.id, fn.id, 'host-blobs')
  scope: hostStore
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b')
    principalId: fn.identity.principalId
    principalType: 'ServicePrincipal'
  }
}
resource quarantineRights 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(quarantine.id, fn.id, 'quarantine-writer')
  scope: quarantine
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
    principalId: fn.identity.principalId
    principalType: 'ServicePrincipal'
  }
}
output projectorPrincipal string = fn.identity.principalId
output dashboardPrincipal string = web.identity.principalId
output dashboardHost string = web.properties.defaultHostName
output functionApp string = fn.name
output jobName string = job.name
output sqlHost string = sql.properties.fullyQualifiedDomainName
