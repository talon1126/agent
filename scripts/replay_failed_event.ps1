param(
  [string]$EventId = "evt_mock_api_failure",
  [string]$MockApiUrl = "http://localhost:8002"
)

Invoke-RestMethod -Method Post -Uri "$MockApiUrl/replay/$EventId" | ConvertTo-Json -Depth 5
