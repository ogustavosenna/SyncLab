@echo off
echo.
echo  Building SyncLab...
echo  ====================
echo.

pyinstaller synclab.spec --noconfirm

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo  BUILD FAILED
    exit /b 1
)

echo.
echo  Build complete!
echo  Output: dist\SyncLab\SyncLab.exe
echo.
