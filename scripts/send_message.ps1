param(
    [string]$MessageFile = "fixtures\messages\order_status_text.json",
    [string]$Url = "http://localhost:5678/webhook/message-agent"
)

$payload = Get-Content -Raw $MessageFile
Invoke-RestMethod -Method Post -Uri $Url -ContentType "application/json" -Body $payload
