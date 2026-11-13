using './main.bicep'

param deploymentStage = 'dev'
param location = readEnvironmentVariable('LAB_LOCATION')
param sqlAdminGroupName = readEnvironmentVariable('SQL_ADMIN_GROUP_NAME')
param sqlAdminGroupObjectId = readEnvironmentVariable('SQL_ADMIN_GROUP_ID')
param functionImage = readEnvironmentVariable('FUNCTION_IMAGE')
param dashboardImage = readEnvironmentVariable('WEB_IMAGE')
param mutationImage = readEnvironmentVariable('MUTATION_IMAGE')
param operatorIp = readEnvironmentVariable('OPERATOR_IP')
