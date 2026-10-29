@echo off
setlocal
cd /d "%~dp0android_client"
where java >nul 2>nul || (echo Java is not installed. Install JDK 21 first.& exit /b 1)
where gradle >nul 2>nul || (echo Gradle is not installed. Use Android Studio or the GitHub Actions workflow included in this project.& exit /b 1)
gradle --no-daemon assembleDebug
if errorlevel 1 exit /b %errorlevel%
echo.
echo APK created at:
echo %CD%\app\build\outputs\apk\debug\app-debug.apk
endlocal
