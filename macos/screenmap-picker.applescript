-- Screen Map 런처 — 아이콘을 누르면 안드로이드 프로젝트 폴더를 고르게 하고, 추출 → 검증 → 빌드한 지도를 브라우저로 연다.
-- 폴더를 아이콘(Dock 포함)에 끌어다 놓아도 된다. 터미널 시험: osascript screenmap-picker.applescript <repo dir>
-- 빌드: osacompile -o "Screen Map.app" screenmap-picker.applescript   (.app 은 git 제외 — 이 소스가 원본)
-- .app 은 이 폴더(macos/)에 두고 Dock 에는 끌어서 등록한다(옮기면 옆의 screenmap-run.sh 를 못 찾는다).
-- 실제 일은 같은 폴더의 screenmap-run.sh 가 한다. 결과: <도구>/out/<프로젝트 이름>/ (지도 HTML · graph.json · code.json · run.log)

on run argv
	-- Finder 더블클릭은 argv 가 리스트가 아니라 class 조차 못 읽는 값으로 온다(-1728) — 인자는 try 안에서만 본다
	set repo to ""
	try
		if (count of argv) > 0 then set repo to item 1 of argv
	end try
	if repo is "" then
		try
			set repo to POSIX path of (choose folder with prompt "지도를 만들 안드로이드(Kotlin) 프로젝트 폴더를 고르세요")
		on error number -128 -- 취소
			return
		end try
	end if
	makeMap(repo)
end run

on open theItems
	makeMap(POSIX path of (item 1 of theItems))
end open

on makeMap(repo)
	set here to do shell script "dirname " & quoted form of POSIX path of (path to me)
	set projectName to do shell script "basename " & quoted form of repo
	set logFile to here & "/../out/" & projectName & "/run.log"
	display notification projectName with title "Screen Map" subtitle "추출 → 검증 → 빌드 중…"

	-- run.sh 의 exit 1(빌드됨·verify 실패)을 do shell script 의 오류로 받지 않도록 종료 코드를 마지막 줄로 함께 받는다
	set outp to do shell script quoted form of (here & "/screenmap-run.sh") & " " & quoted form of repo & " 2>/dev/null; echo \"exit=$?\""
	set ps to paragraphs of outp
	set rc to (text 6 thru -1 of (last item of ps)) as integer
	set html to ""
	if (count of ps) > 1 then set html to item ((count of ps) - 1) of ps

	if rc = 2 or html = "" then
		set tailText to do shell script "tail -n 8 " & quoted form of logFile & " 2>/dev/null || true"
		display dialog "지도를 만들지 못했습니다 — 안드로이드(Kotlin) 프로젝트 폴더가 맞는지 확인하세요." & return & return & tailText & return & return & "로그: " & logFile buttons {"확인"} default button 1 with icon stop with title "Screen Map"
		return
	end if

	do shell script "open " & quoted form of html
	if rc is not 0 then
		set fails to do shell script "grep '^FAIL' " & quoted form of logFile & " || true"
		display dialog "지도는 열었지만 verify 가 실패했습니다 — 설정(screenmap.json)이 이 앱과 안 맞을 수 있습니다." & return & return & fails & return & return & "로그: " & logFile buttons {"확인"} default button 1 with icon caution with title "Screen Map"
	end if
end makeMap
