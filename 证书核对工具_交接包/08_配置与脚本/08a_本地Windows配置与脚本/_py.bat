@echo off
set "PYEXE="
for %%V in (3.12 3.11 3.10 3.13) do (
    if not defined PYEXE ( py -%%V -c "import openpyxl" >nul 2>&1 && set "PYEXE=py -%%V" )
)
if not defined PYEXE ( python -c "import openpyxl" >nul 2>&1 && set "PYEXE=python" )
if not defined PYEXE (
    for %%V in (3.12 3.11 3.10 3.13) do (
        if not defined PYEXE ( py -%%V -m pip --version >nul 2>&1 && set "PYEXE=py -%%V" )
    )
)
if not defined PYEXE ( python -m pip --version >nul 2>&1 && set "PYEXE=python" )
if not defined PYEXE ( py -c "import sys" >nul 2>&1 && set "PYEXE=py" )
if not defined PYEXE ( python -c "import sys" >nul 2>&1 && set "PYEXE=python" )
