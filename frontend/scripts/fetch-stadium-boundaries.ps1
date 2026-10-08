# Read-only public OSM geometry retrieval -> generated dataset, no user data.
$ErrorActionPreference = 'Stop'
$stadiumRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$stadiumSource = Get-Content -LiteralPath (Join-Path $stadiumRoot 'data/preprocessed/stadium_locations.json') -Raw | ConvertFrom-Json
$stadiumOutput = [ordered]@{}
foreach ($stadiumProperty in $stadiumSource.stadiums.PSObject.Properties) {
    $stadiumCode = $stadiumProperty.Name
    $stadiumPoint = $stadiumProperty.Value
    $stadiumUrls = @($stadiumPoint.boundarySources)
    if (-not $stadiumPoint.boundarySources) { $stadiumUrls = @("https://www.openstreetmap.org/$($stadiumPoint.osmType)/$($stadiumPoint.osmId)") }
    $stadiumRings = [Collections.Generic.List[object]]::new()
    foreach ($stadiumSourceUrl in $stadiumUrls) {
        if ($stadiumSourceUrl -notmatch '^https://www\.openstreetmap\.org/(way|relation)/(\d+)$') { throw "Unexpected geometry source: $stadiumSourceUrl" }
        $stadiumType = $Matches[1]; $stadiumId = $Matches[2]
        $stadiumData = Invoke-RestMethod -Uri "https://api.openstreetmap.org/api/0.6/$stadiumType/$stadiumId/full.json" -TimeoutSec 20
        $stadiumNodes = @{}; $stadiumWays = @{}
        foreach ($stadiumElement in $stadiumData.elements) {
            if ($stadiumElement.type -eq 'node') { $stadiumNodes[[string]$stadiumElement.id] = @([double]$stadiumElement.lat,[double]$stadiumElement.lon) }
            if ($stadiumElement.type -eq 'way') { $stadiumWays[[string]$stadiumElement.id] = $stadiumElement }
        }
        $stadiumFeature = $stadiumData.elements | Where-Object { $_.type -eq $stadiumType -and [string]$_.id -eq $stadiumId }
        $stadiumOuters = @($stadiumFeature)
        if ($stadiumType -eq 'relation') { $stadiumOuters = @($stadiumFeature.members | Where-Object { $_.type -eq 'way' -and $_.role -eq 'outer' } | ForEach-Object { $stadiumWays[[string]$_.ref] }) }
        foreach ($stadiumWay in $stadiumOuters) {
            $stadiumRing = [Collections.Generic.List[object]]::new()
            foreach ($stadiumNodeId in $stadiumWay.nodes) {
                $stadiumCoordinate = $stadiumNodes[[string]$stadiumNodeId]
                if (-not $stadiumCoordinate) { throw "$stadiumCode missing node" }
                $stadiumRing.Add($stadiumCoordinate)
            }
            if ($stadiumRing.Count -lt 4 -or $stadiumRing[0][0] -ne $stadiumRing[$stadiumRing.Count-1][0] -or $stadiumRing[0][1] -ne $stadiumRing[$stadiumRing.Count-1][1]) { throw "$stadiumCode closed outer ring required" }
            $stadiumRings.Add($stadiumRing.ToArray())
        }
    }
    $stadiumOutput[$stadiumCode] = @{sources=$stadiumUrls;rings=$stadiumRings.ToArray()}
    Write-Output "$stadiumCode : $($stadiumRings.Count) facility/field rings"
}
$stadiumGenerated = @{checkedAt=$stadiumSource.checkedAt;licenseUrl=$stadiumSource.licenseUrl;stadiums=$stadiumOutput} | ConvertTo-Json -Depth 10
[IO.File]::WriteAllText((Join-Path $stadiumRoot 'data/preprocessed/stadium_boundaries.json'), $stadiumGenerated, [Text.UTF8Encoding]::new($false))
