# 리센느 일정 → 구글 캘린더

GitHub Actions가 1시간마다 아래 출처를 확인해서 구글 캘린더에 일정을 넣어줍니다.

| 출처 | 내용 |
|---|---|
| Mnet Plus 리센느 공식 일정 | 지난달 ~ 6개월 뒤 일정. 시간이 바뀌면 수정, 취소되면 삭제 |
| 유튜브 (RESCENE 공식, 원이 개인 채널) | 예정된 라이브·프리미어 |
| Mnet Plus Notice 게시판 + 유튜브 커뮤니티 게시물 | AI(Gemini)가 글을 읽고 날짜·시간이 있는 일정만 추가. 제목 앞에 `[공지]`, `[유튜브 공지]` |

- 이 프로그램이 만든 일정만 건드려요. 같은 캘린더에 직접 만든 일정은 안전해요.
- 캘린더에서 직접 지운 일정은 다시 넣지 않아요.
- AI가 읽은 글은 `state/ai_cache.json`에 기록해 두고, 새 글이나 고쳐진 글만 다시 읽어요.

## 처음 설정하기

### 1. Google Cloud 프로젝트 만들기
1. https://console.cloud.google.com 접속 → 위쪽 프로젝트 선택 → **새 프로젝트** → 이름 `rescene-calendar` → 만들기
2. 왼쪽 메뉴 **API 및 서비스 → 라이브러리**에서 아래 두 개를 각각 검색해서 **사용** 클릭
   - `Google Calendar API`
   - `YouTube Data API v3`

### 2. 유튜브 API 키
1. **API 및 서비스 → 사용자 인증 정보 → + 사용자 인증 정보 만들기 → API 키**
2. 만들어진 키의 **키 수정** → API 제한사항에서 **키 제한** → `YouTube Data API v3`만 체크 → 저장
3. 키 값을 복사해 둡니다 (→ `YOUTUBE_API_KEY`)

### 3. 서비스 계정 (프로그램 전용 구글 계정)
1. **API 및 서비스 → 사용자 인증 정보 → + 사용자 인증 정보 만들기 → 서비스 계정**
2. 이름 `rescene-sync` → 만들고 계속하기 → (역할은 비워두고) 완료
3. 만들어진 서비스 계정 클릭 → **키** 탭 → **키 추가 → 새 키 만들기 → JSON** → 파일이 다운로드됨
   - ⚠️ 이 파일은 비밀번호와 같아요. 다른 곳에 올리거나 공유하지 마세요.
4. 서비스 계정의 이메일 주소(`rescene-sync@….iam.gserviceaccount.com`)를 복사해 둡니다

### 4. 리센느 전용 캘린더
1. https://calendar.google.com → 왼쪽 **다른 캘린더 +** → **새 캘린더 만들기** → 이름 `RESCENE`
2. 만든 캘린더의 **설정 및 공유** → **특정 사용자 또는 그룹과 공유** → **사용자 추가**
   → 3-4에서 복사한 서비스 계정 이메일, 권한 **일정 변경** → 보내기
3. 같은 화면 아래 **캘린더 통합 → 캘린더 ID**를 복사 (→ `GOOGLE_CALENDAR_ID`)

### 5. Gemini API 키 (공지 읽기용, 무료)
1. https://aistudio.google.com/apikey 접속 → **API 키 만들기**
2. 프로젝트는 1번에서 만든 `rescene-calendar` 선택 → 만들어진 키 복사 (→ `GEMINI_API_KEY`)
   - 결제 정보를 등록하지 않으면 무료 등급으로만 돌아서 돈이 나가지 않아요.

### 6. GitHub Secrets 넣기
저장소 → **Settings → Secrets and variables → Actions → New repository secret**

| 이름 | 값 |
|---|---|
| `GOOGLE_SERVICE_ACCOUNT_JSON` | 3번에서 받은 JSON 파일을 텍스트 편집기로 열어서 **내용 전체** |
| `GOOGLE_CALENDAR_ID` | 4-3의 캘린더 ID |
| `YOUTUBE_API_KEY` | 2-3의 API 키 |
| `GEMINI_API_KEY` | 5-2의 Gemini API 키 |

### 7. 첫 실행
저장소 → **Actions → 리센느 일정 동기화 → Run workflow**. 이후로는 1시간마다 자동으로 돌아요.

## 내 컴퓨터에서 시험해보기

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m rescene_sync      # 구글 설정 없이 돌리면 '연습 모드'(목록만 출력)
```

## 알아둘 점
- Mnet Plus는 공식 공개 API가 아니라 사이트 내부 주소를 써요. 사이트가 바뀌면 실행이 실패(빨간 X)하고, GitHub가 이메일로 알려줘요.
- 유튜브 커뮤니티 게시물은 공식 API가 없어서 웹페이지를 읽어요. 유튜브가 페이지를 바꾸면 그 부분만 실패해요.
- AI가 뽑은 일정은 틀릴 수 있어요. 일정 설명에 원문 링크가 있으니 확인해 주세요. 잘못 들어간 일정은 캘린더에서 지우면 다시 안 들어와요.
- Gemini 무료 등급은 입력한 글이 구글의 서비스 개선에 쓰일 수 있어요 (공개된 공지만 보내요).
- 공개 저장소는 60일 동안 변화가 없으면 자동 실행이 멈추는데, 워크플로가 45일마다 빈 기록을 남겨서 막아요.
