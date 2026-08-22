
param([Parameter(Mandatory=$true)][string]$Payload)
$temp = $env:TEMP
$outFile = Join-Path $temp "mcp_call_out.txt"
$json = $Payload | ConvertTo-Json -Depth 12
$json | & "D:\Developer\Hoolinks\hoolinks-plugin\mcp\.venv\Scripts\python.exe" (Join-Path $temp "mcp_call_uv.py") 2>&1 | Out-File -FilePath $outFile -Encoding utf8
Get-Content $outFile -Raw
