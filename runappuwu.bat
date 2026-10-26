@echo off
cd /d "%~dp0"

set "GIPHY_API_KEY=1FBeJVZvq00WZMYZoIQiUsWvpe9rpS9R"
py -m pip install -r requirements.txt
py app.py
pause
