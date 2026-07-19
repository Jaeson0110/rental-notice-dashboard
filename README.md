# 우리집 임대공고

LH·SH·GH의 임대주택 공고를 매일 수집하고, 두 사람이 관심 공고·진행 상태·메모를 함께 관리하는 개인용 웹 대시보드입니다.

## 현재 포함된 기능

- LH·SH·GH 공식 공고 페이지 수집기
- 기관별 수집 실패 시 해당 기관의 기존 데이터 보존
- 기관·지역·임대유형·상태 필터
- 오늘 신규, 접수 중, 7일 이내 마감 집계
- 관심 공고와 `검토 필요 → 신청 예정 → 서류 준비 중 → 신청 완료` 단계 관리
- 공고별 공동 메모
- Supabase 이메일 매직링크 로그인 및 두 사용자 공동 동기화
- GitHub Actions 매일 오전 7시 10분 자동 수집·배포
- Supabase 미설정 시 브라우저 localStorage에 저장되는 데모 모드

> `data/notices.json`에는 최초 화면 확인을 위한 샘플 데이터가 포함되어 있습니다. 자동 수집이 한 번 성공하면 공식 사이트 수집 결과로 교체됩니다. 제목에 `[화면 확인용 샘플]`이 붙은 항목은 실제 신청에 사용하면 안 됩니다.

## 1. 컴퓨터에서 바로 확인

압축을 푼 폴더에서 터미널을 열고 다음 명령을 실행합니다.

```bash
python3 -m http.server 8000
```

브라우저에서 `http://localhost:8000`을 엽니다. 이 상태는 데모 모드이며 관심·메모가 현재 브라우저에만 저장됩니다.

## 2. 수집기 테스트

```bash
python3 -m pip install -r collector/requirements.txt
python3 collector/main.py --output data/notices.json
```

수집기가 인터넷에 접속할 수 있으면 공식 페이지에서 데이터를 가져옵니다. 한 기관의 페이지 구조가 바뀌거나 일시적으로 차단되어도 다른 기관 수집은 계속되고, 실패한 기관의 기존 데이터는 삭제하지 않습니다.

수집 결과와 오류는 `data/notices.json`의 `sourceStatus`에서 확인할 수 있습니다.

## 3. Supabase 공동 모드 설정

### 3-1. 프로젝트 생성

Supabase에서 새 프로젝트를 생성합니다.

### 3-2. 데이터베이스 설정

Supabase의 **SQL Editor**에서 `supabase/schema.sql` 전체를 실행합니다. 파일 하단의 이메일 등록문을 실제 두 사람 이메일로 바꾸어 한 번 더 실행합니다.

```sql
insert into public.allowed_users (email, display_name) values
  ('본인이메일@example.com', '재웅'),
  ('여자친구이메일@example.com', '여자친구')
on conflict (email) do update set display_name = excluded.display_name;
```

### 3-3. 로그인 주소 설정

Supabase에서 다음 메뉴로 이동합니다.

`Authentication → URL Configuration`

- Site URL: 배포할 사이트 주소
- Redirect URLs: 같은 사이트 주소와 `/**` 패턴

예시:

```text
https://계정명.github.io/저장소이름/
https://계정명.github.io/저장소이름/**
```

### 3-4. 웹 설정 연결

`config.js`를 열어 Supabase 프로젝트의 URL과 anon key를 넣습니다.

```javascript
window.APP_CONFIG = {
  supabaseUrl: "https://프로젝트ID.supabase.co",
  supabaseAnonKey: "Supabase anon key",
  appName: "우리집 임대공고",
  loginRedirectUrl: window.location.origin + window.location.pathname
};
```

Supabase의 anon key는 브라우저에서 사용하는 공개 키입니다. `service_role` 키는 절대로 넣으면 안 됩니다. 실제 데이터 접근 제한은 `schema.sql`의 RLS 정책이 담당합니다.

## 4. LH 공식 API 키 연결 — 권장

LH는 공식 공공데이터 API 키가 있을 때 API를 우선 사용하고, 키가 없거나 API가 실패하면 LH청약플러스 공고 페이지 수집으로 전환됩니다.

1. 공공데이터포털의 `한국토지주택공사_분양임대공고문 조회 서비스` 활용신청을 합니다.
2. 발급된 **일반 인증키(Decoding)**를 복사합니다.
3. GitHub 저장소의 `Settings → Secrets and variables → Actions`에서 새 Repository secret을 만듭니다.
4. 이름은 `DATA_GO_KR_SERVICE_KEY`, 값은 발급 키로 입력합니다.

인증키는 `config.js`나 소스 파일에 직접 넣지 않습니다.

## 5. GitHub Pages 배포

1. GitHub에서 새 저장소를 만듭니다.
2. 이 폴더의 전체 파일을 저장소에 올립니다.
3. 저장소의 `Settings → Pages`로 이동합니다.
4. Source를 **GitHub Actions**로 선택합니다.
5. `Actions` 탭에서 `Update rental notices and deploy` 작업을 한 번 직접 실행합니다.
6. 배포가 끝나면 Pages 주소를 여자친구와 공유합니다.

워크플로는 매일 **Asia/Seoul 오전 7시 10분**에 실행됩니다. `Actions` 화면의 `Run workflow` 버튼으로 수동 갱신도 가능합니다.

## 6. 파일 구조

```text
.
├── index.html                     # 대시보드 화면
├── config.js                      # Supabase 연결 설정
├── assets/
│   ├── app.js                     # 필터·공동 상태·로그인 기능
│   └── styles.css                 # 반응형 디자인
├── data/notices.json              # 수집된 공고 데이터
├── collector/
│   ├── main.py                    # 수집 실행 및 기존 데이터 보존
│   ├── normalize.py               # 기관별 데이터를 공통 형식으로 정리
│   └── sources/                   # LH·SH·GH 수집원
├── supabase/schema.sql            # 공동 사용 DB와 접근정책
└── .github/workflows/
    └── update-notices.yml         # 매일 자동 수집 및 Pages 배포
```

## 운영상 주의사항

- 이 서비스는 공고 탐색을 돕는 용도이며, 신청 자격과 일정은 반드시 연결된 공식 공고문을 최종 확인해야 합니다.
- 기관 홈페이지 구조가 바뀌면 해당 수집기의 선택자나 정규화 규칙을 수정해야 할 수 있습니다.
- 현재 로그인 화면은 일반 사용자의 접근을 막지만, GitHub Pages의 정적 `data/notices.json` 자체를 암호화하지는 않습니다. 관심 상태와 메모는 Supabase RLS로 두 이메일에만 제한됩니다.
- 공고 목록 자체까지 완전히 비공개로 만들려면 이후 Cloudflare Access를 사이트 앞단에 추가하는 방식이 적합합니다.
- 주민등록번호, 소득 증빙, 계약서 같은 민감한 개인정보는 메모에 저장하지 않는 것을 권장합니다.

## 공식 수집원

- LH 임대주택 공고: `https://apply.lh.or.kr/lhapply/apply/wt/wrtanc/selectWrtancList.do?mi=1026`
- SH 공공임대 공고: `https://housing.seoul.go.kr/site/main/sh/publicLease/list`
- GH 분양·임대 공고: `https://www.gh.or.kr/gh/announcement-of-salerental001.do`
