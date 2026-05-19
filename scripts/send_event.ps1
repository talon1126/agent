param(
  [string]$EventFile = "fixtures/events/refund_high_value.json",
  [string]$WebhookUrl = "http://localhost:5678/webhook/after-sales-event"
)

$payload = Get-Content -Raw $EventFile
Invoke-RestMethod -Method Post -Uri $WebhookUrl -ContentType "application/json" -Body $payload | ConvertTo-Json -Depth 10
