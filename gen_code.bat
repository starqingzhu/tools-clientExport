Cd /d %~dp0
echo %CD%

set WORKSPACE=../../
set LUBAN_DLL=%WORKSPACE%\Luban\Luban.dll
set CONF_ROOT=.
set DATA_OUTPATH=%CONF_ROOT%/json_out


dotnet %LUBAN_DLL% ^
     -t client ^
    --conf %CONF_ROOT%\luban.conf ^
    -x code.lineEnding=crlf ^
    -d json^
	-x outputDataDir=%DATA_OUTPATH% 
pause

