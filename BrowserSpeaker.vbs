' Copyright 2026 Dogukan Sahil
' Licensed under the Apache License, Version 2.0.
' See LICENSE file in the project root, or http://www.apache.org/licenses/LICENSE-2.0
'
' BrowserSpeaker launcher — double-click to run without a terminal
Option Explicit

Dim sh, fso, scriptDir, venv, pyw, py
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)

venv = scriptDir & "\.venv"
pyw  = venv & "\Scripts\pythonw.exe"
py   = venv & "\Scripts\python.exe"

' First run: create venv + install dependencies (hidden, blocking)
If Not fso.FolderExists(venv) Then
    sh.Popup "BrowserSpeaker first-time setup is running, this only happens once (~1-2 min)...", _
             3, "BrowserSpeaker", 64
    sh.Run "cmd /c """"" & "python -m venv """ & venv & """ && """ & py & """ -m pip install --upgrade pip && """ & py & """ -m pip install -r """ & scriptDir & "\requirements.txt""""""", 0, True
End If

' Launch silently
If fso.FileExists(pyw) Then
    sh.CurrentDirectory = scriptDir
    sh.Run """" & pyw & """ """ & scriptDir & "\server.py""", 0, False
Else
    sh.Popup "Setup failed. Run 'python -m venv .venv && .venv\Scripts\pip install -r requirements.txt' manually.", _
             5, "BrowserSpeaker", 16
End If
