; ---------------------------------------------------------------
;  Mimo 桌宠 - Inno Setup 安装脚本
;  前置步骤：python build_exe.py   生成 dist\MimoPet.exe
;  编译命令：iscc mimo_setup.iss    产出 out\Mimo桌宠安装包-1.1.0.exe
; ---------------------------------------------------------------

#define AppName "Mimo 桌宠"
#define AppVersion "1.1.0"
#define AppExe "MimoPet.exe"
#define AppId "8E4C1D22-9A3B-4F6E-8C71-2B5D7A0F1E33"

[Setup]
AppId={{{#AppId}}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=MiMo 桌面伙伴
DefaultDirName={localappdata}\Programs\MimoPet
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
AllowNoIcons=yes
PrivilegesRequired=lowest
OutputDir=out
OutputBaseFilename=Mimo桌宠安装包-{#AppVersion}
SetupIconFile=app\mimo.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
DisableWelcomePage=no

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"

; ---- 界面文案本地化（官方未提供中文语言包，故用英文底 + 逐条覆盖）----
[Messages]
en.SetupAppTitle=Mimo 桌宠 安装程序
en.SetupWindowTitle=Mimo 桌宠 安装程序 (%1)
en.WelcomeLabel1=欢迎安装 Mimo 桌宠
en.WelcomeLabel2=这个向导将在你的电脑上安装 [name/ver]。%n%n装好之后，你的桌面上会出现一只会自己走动、会回应你的小宠物。%n%n点击“下一步”继续。
en.SelectDirLabel3=安装程序将把 Mimo 桌宠 安装到下面的文件夹。
en.SelectDirBrowseLabel=如果要换一个文件夹，点击“浏览”；否则点击“下一步”。
en.SelectTasksLabel2=请选择要一起完成的附加任务，然后点击“下一步”。
en.SelectTasksDesc=附加任务
en.SelectDirDesc=安装位置
en.ReadyLabel1=一切都准备好了，可以开始安装。
en.ReadyLabel2a=点击“安装”开始安装；如果还想改设置，点“上一步”。
en.PreparingDesc=正在准备安装，请稍候…
en.InstallingLabel=正在安装 Mimo 桌宠，请稍候…
en.FinishedHeadingLabel=Mimo 桌宠 安装完成
en.FinishedLabel=安装已经完成。%n%n如果勾选了“立即运行”，它现在已经在你的桌面上走动了。
en.ClickNext=点击“下一步”继续。
en.ClickFinish=点击“完成”结束安装。
en.ButtonNext=下一步(&N) >
en.ButtonBack=< 上一步(&B)
en.ButtonInstall=安装(&I)
en.ButtonCancel=取消
en.ButtonFinish=完成(&F)
en.ButtonBrowse=浏览(&R)…
en.ButtonYes=是(&Y)
en.ButtonNo=否(&N)
en.ExitSetupTitle=退出安装
en.ExitSetupMessage=安装还没有完成，确定要退出吗？%n%n以后可以重新运行安装程序继续。

[Tasks]
Name: "desktopicon"; Description: "在桌面上创建快捷方式"; GroupDescription: "附加任务："; Flags: checkedonce
Name: "startupicon"; Description: "开机时自动启动桌宠"; GroupDescription: "附加任务："

[Files]
Source: "dist\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "app\使用说明.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "app\停止桌宠.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "app\mimo.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Mimo 桌宠"; Filename: "{app}\{#AppExe}"; IconFilename: "{app}\mimo.ico"
Name: "{group}\使用说明"; Filename: "{app}\使用说明.txt"
Name: "{group}\卸载 Mimo 桌宠"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Mimo 桌宠"; Filename: "{app}\{#AppExe}"; IconFilename: "{app}\mimo.ico"; Tasks: desktopicon
Name: "{userstartup}\Mimo 桌宠"; Filename: "{app}\{#AppExe}"; IconFilename: "{app}\mimo.ico"; Tasks: startupicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "立即运行 Mimo 桌宠"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "taskkill.exe"; Parameters: "/f /im {#AppExe}"; Flags: runhidden; RunOnceId: "KillPet"

[Code]
{ 装之前先结束正在运行的桌宠，否则文件被占用会装不上 }
function InitializeSetup(): Boolean;
var
  ResultCode: Integer;
begin
  Exec('taskkill.exe', '/f /im {#AppExe}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := True;
end;