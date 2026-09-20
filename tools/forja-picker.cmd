@echo off
rem Inicia o forja-picker (seletor de pasta) e o forja-runner (comandos e servidores no Windows) em segundo plano.
rem Para abrir junto com o Windows: Win+R -> shell:startup e cole um atalho para este arquivo.
start "" pythonw "%~dp0forja_picker.py"
start "" pythonw "%~dp0forja_runner.py"
