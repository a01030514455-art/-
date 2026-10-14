# 무료 소스 라이선스·API 조건 (2026-10 확인)

앱은 아래 조건을 기준으로 만들었습니다. 약관은 바뀔 수 있으니 실제로 쓰기 전에 각 공식 페이지를 다시 확인하세요.

## 스톡 영상 (보조 장면 전용, 블로그 원본 사진이 기본)

| 출처 | 앱에서 쓰는 방법 | 라이선스 요약 | API 조건 |
|---|---|---|---|
| **Pexels** | 공식 API로 검색 → 사용자가 고른 1개만 다운로드 | 무료·상업 이용 가능. 변형 없이 재판매·재배포 금지 | 무료 키 필요. 기본 시간당 200회·월 2만 회. 결과 화면에 Pexels 링크와 작가 표기. Pexels를 대신하는 스톡 사이트처럼 쓰는 것 금지 |
| **Pixabay** | 공식 API로 검색 → 사용자가 고른 1개만 다운로드 | 무료·상업 이용 가능, 출처 표기 불필요. 단독 재판매·재배포 금지 | 무료 키 필요. 기본 60초당 100회. 검색 결과 24시간 캐시. 결과 화면에 출처 표시. 대량 자동 다운로드 금지 |
| **Mixkit** | **공식 API 없음 → 자동 검색·다운로드 안 함.** 사이트를 열어 주고, 사용자가 직접 받은 파일을 출처 URL과 함께 등록 | Free License 영상만 상업 이용 가능, 출처 표기 불필요. Restricted License는 상업 이용 불가. 원본 재판매·재배포 금지 | 없음 |

- 모든 다운로드·등록은 `output/stock_log.jsonl`(전체)과 `output/<작업ID>/job.json`(작업별)에 출처 URL·작가·라이선스·시각과 함께 기록됩니다.
- 영상에 보조 영상을 쓰면 같은 폴더에 `credits.txt`(출처 문구)가 만들어집니다. 업로드할 때 설명란에 붙이면 됩니다.
- 원본 파일을 따로 내보내거나 일괄로 받는 기능은 없습니다.

## 한국어 AI 음성

| 엔진 | 비용 | 비고 |
|---|---|---|
| **Edge 무료 음성** (기본) | 무료, 키 불필요 | 비공식 경로라 서비스가 바뀌면 멈출 수 있음 |
| **Azure AI Speech F0** | 무료, 월 50만 자 | Azure Portal에서 Speech 리소스를 F0(무료)로 생성. 한도를 넘으면 요청이 거절될 뿐 과금되지 않음. F0는 1분에 20회 요청 제한 |
| Azure AI Speech S0 | **유료**, 첫 글자부터 과금 | 앱이 예상 비용을 보여 주고 승인했을 때만 사용 |
| PC 내장 음성 | 무료, 오프라인 | 음질 낮음 |

- Azure 글자 수는 보수적으로 셉니다: 한글·한자·가나 2자, SSML 조절 태그 포함(바깥 `<speak>`·`<voice>` 제외).
- 월 사용량은 `output/tts_usage.json`에 쌓이고, 무료 한도 80%에서 경고, 100%를 넘으면 시작 전에 막습니다.
- 화면의 "이번 영상 비용"에서 **무료 처리 글자 수**와 **과금 대상 글자 수**를 따로 보여 줍니다.

## 참고한 공식 페이지
- Pexels API: https://www.pexels.com/api/documentation/ · 라이선스: https://www.pexels.com/license/
- Pixabay API: https://pixabay.com/api/docs/ · 라이선스: https://pixabay.com/service/license-summary/
- Mixkit 라이선스: https://mixkit.co/license/
- Azure Speech 가격: https://azure.microsoft.com/pricing/details/cognitive-services/speech-services/
