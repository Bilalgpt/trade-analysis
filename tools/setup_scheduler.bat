@echo off
schtasks /create /tn "SOL_Hourly_Alert" /tr "\"C:\Program Files\Python312\python.exe\" \"C:\Projects\crypto explore\solana\sol_email_alert.py\"" /sc hourly /mo 1 /f
if %errorlevel%==0 (
    echo.
    echo SUCCESS! SOL hourly email alert scheduled.
    echo It will run every hour and email muhammadbilalafzal1@gmail.com
    echo.
    echo To check it is running:  schtasks /query /tn "SOL_Hourly_Alert"
    echo To stop it:              schtasks /delete /tn "SOL_Hourly_Alert" /f
    echo To run it now manually:  schtasks /run /tn "SOL_Hourly_Alert"
) else (
    echo FAILED. Try running this file as Administrator.
)
pause
