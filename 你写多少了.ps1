$py = (Get-ChildItem -Recurse -Filter *.py | Where-Object { $_.FullName -notmatch 'venv' } | Get-Content | Measure-Object -Line).Lines
$js = (Get-ChildItem -Recurse -Filter *.js | Where-Object { $_.FullName -notmatch 'venv' } | Get-Content | Measure-Object -Line).Lines
$html = (Get-ChildItem -Recurse -Filter *.html | Where-Object { $_.FullName -notmatch 'venv' } | Get-Content | Measure-Object -Line).Lines
$css = (Get-ChildItem -Recurse -Filter *.css | Where-Object { $_.FullName -notmatch 'venv' } | Get-Content | Measure-Object -Line).Lines
Write-Host ""
Write-Host "Python: $py"
Write-Host ""
Write-Host "JavaScript: $js"
Write-Host "HTML: $html"
Write-Host "CSS: $css"
Write-Host ""
Write-Host "Total: $($py + $js + $html + $css)"
Write-Host ""