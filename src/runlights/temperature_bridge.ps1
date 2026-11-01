param([Parameter(Mandatory = $true)][string]$DllPath)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$computer = $null
function Write-Response($value) {
    [Console]::Out.WriteLine(($value | ConvertTo-Json -Compress))
    [Console]::Out.Flush()
}
function Get-Tree($items) {
    foreach ($item in $items) {
        $item.Update()
        $item
        Get-Tree $item.SubHardware
    }
}
try {
    Add-Type -Path $DllPath
    $computer = [LibreHardwareMonitor.Hardware.Computer]::new()
    $computer.IsCpuEnabled = $true
    $computer.IsGpuEnabled = $true
    $computer.Open()
    Write-Response @{ok = $true}
    while ($null -ne ($line = [Console]::In.ReadLine())) {
        try {
            $cpu = @(); $gpu = @(); $preferredCpu = @(); $preferredGpu = @()
            foreach ($hardware in (Get-Tree $computer.Hardware)) {
                foreach ($sensor in $hardware.Sensors) {
                    if ($sensor.SensorType.ToString() -ne 'Temperature' -or $null -eq $sensor.Value) { continue }
                    if ($hardware.HardwareType.ToString() -match 'Cpu') {
                        $cpu += $sensor.Value
                        if ($sensor.Name -match 'Package|Tctl|Tdie') { $preferredCpu += $sensor.Value }
                    } elseif ($hardware.HardwareType.ToString() -match 'Gpu') {
                        # Do not substitute hotspot or memory temperatures for GPU core.
                        if ($sensor.Name -match 'Core') { $preferredGpu += $sensor.Value }
                    }
                }
            }
            if ($preferredCpu.Count) { $cpu = $preferredCpu }
            $gpu = $preferredGpu
            Write-Response @{
                ok = $true
                cpu = if ($cpu.Count) { ($cpu | Measure-Object -Maximum).Maximum } else { $null }
                gpu = if ($gpu.Count) { ($gpu | Measure-Object -Maximum).Maximum } else { $null }
            }
        } catch { Write-Response @{ok = $false; error = $_.Exception.Message} }
    }
} catch { Write-Response @{ok = $false; error = $_.Exception.Message} }
finally { if ($null -ne $computer) { $computer.Close() } }
