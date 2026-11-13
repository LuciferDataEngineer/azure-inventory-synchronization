// Negative control. The public-endpoint lab cannot accept production deployment.
using './main.bicep'
extends './dev.bicepparam'

param deploymentStage = 'prod'
