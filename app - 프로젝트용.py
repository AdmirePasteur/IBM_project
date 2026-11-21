import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import folium
import math
import re
import inspect
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))  # 감사 로그 시각을 한국 시간으로 고정 (Colab 서버는 UTC)

# IBM watsonx.ai SDK (미설치 환경에서도 앱이 멈추지 않도록 예외 처리)
try:
    from ibm_watsonx_ai.foundation_models import ModelInference
    from ibm_watsonx_ai import Credentials
    WATSONX_AVAILABLE = True
except ImportError:
    WATSONX_AVAILABLE = False

# ------------------------------------------
# 실행 방법 자동 처리: VS Code '▶ Run Python File' 또는 `python app.py`로 실행해도
# 내부에서 `streamlit run app.py`로 다시 띄워 줍니다. (Colab은 K_Wildfire_Shield_Colab.ipynb 사용)
# ------------------------------------------
if __name__ == "__main__":
    try:
        from streamlit.runtime import exists as _st_running
    except ImportError:
        _st_running = lambda: False
    if not _st_running():
        import os, sys, subprocess
        sys.exit(subprocess.call([sys.executable, "-m", "streamlit", "run", os.path.abspath(__file__)] + sys.argv[1:]))

# ==========================================
# 1. 페이지 기본 설정 & 세션 상태 초기화
# ==========================================
st.set_page_config(page_title="K-Wildfire Shield", page_icon="🔥", layout="wide")

if "audit_logs" not in st.session_state:
    st.session_state.audit_logs = []
if "auto_launched" not in st.session_state:
    st.session_state.auto_launched = set()

# Streamlit 버전 호환 헬퍼 (구버전/신버전 모두 동작)
_BUTTON_HAS_WIDTH = "width" in inspect.signature(st.button).parameters

def wide_kwargs():
    return {"width": "stretch"} if _BUTTON_HAS_WIDTH else {"use_container_width": True}

def render_html(html: str, height: int):
    if hasattr(st, "iframe"):
        st.iframe(html, height=height)
    else:
        components.html(html, height=height)

def to_bool(v, default=False) -> bool:
    """CSV 값("False", "0", "N", 빈칸 등)을 안전하게 bool로 변환"""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return default
    if isinstance(v, str):
        t = v.strip().lower()
        if t in ("true", "1", "y", "yes", "t", "o", "예"):
            return True
        if t in ("false", "0", "n", "no", "f", "x", "아니오", ""):
            return False
        return default
    return bool(v)

def to_float(v, default: float) -> float:
    try:
        f = float(v)
        return default if math.isnan(f) else f
    except (TypeError, ValueError):
        return default

# ==========================================
# 2. 내장 샘플 데이터 (국내 3건 + 해외 수출 시연 1건)
# ==========================================
DRONE_STATIONS = pd.DataFrame([
    {"id": "ST-KR-01", "name": "전남 동부 거점 스테이션 A", "region": "KR", "lat": 35.0150, "lon": 127.4900, "battery": 95, "payload": "열화상+소화탄 4발"},
    {"id": "ST-KR-02", "name": "전남 동부 거점 스테이션 B", "region": "KR", "lat": 34.9600, "lon": 127.5400, "battery": 88, "payload": "열화상+지연제"},
    {"id": "ST-KR-03", "name": "강원 영동 거점 스테이션 C", "region": "KR", "lat": 37.7600, "lon": 128.8800, "battery": 92, "payload": "열화상+소화탄 4발"},
    {"id": "ST-US-01", "name": "California Base Station Alpha", "region": "US", "lat": 34.0600, "lon": -118.2500, "battery": 96, "payload": "Thermal + Retardant"},
    {"id": "ST-US-02", "name": "California Base Station Beta", "region": "US", "lat": 34.0950, "lon": -118.1800, "battery": 90, "payload": "Thermal Recon"},
])

SAMPLE_SCENARIOS = {
    "① 정상 발화 (국내 산악 지대)": {
        "event_id": "EV-2026-101", "region": "KR", "location": "전남 산악 인접 구역",
        "lat": 34.9950, "lon": 127.5050, "cctv_smoke": True, "humidity": 22,
        "wind_speed": 6.2, "wind_dir": "북서풍(NW)", "duplicate_119": True,
        "near_village": True, "permitted_burn": False,
        "raw_report": "김철수(010-1234-5678)입니다. 뒷산 능선에서 검은 연기가 엄청 올라오고 불길이 보입니다!"
    },
    "② 오경보 차단 (허가된 영농 소각)": {
        "event_id": "EV-2026-102", "region": "KR", "location": "전남 농경지 인접 구역",
        "lat": 34.9720, "lon": 127.5280, "cctv_smoke": True, "humidity": 55,
        "wind_speed": 2.1, "wind_dir": "남풍(S)", "duplicate_119": False,
        "near_village": False, "permitted_burn": True,
        "raw_report": "이영희(010-9876-5432)입니다. 멀리서 연기가 좀 보이는데 산불인지 확인 부탁해요."
    },
    "③ 강풍 가드레일 작동 (드론 비행 차단)": {
        "event_id": "EV-2026-103", "region": "KR", "location": "강원 영동 산악 구역",
        "lat": 37.7520, "lon": 128.8950, "cctv_smoke": True, "humidity": 18,
        "wind_speed": 14.5, "wind_dir": "서풍(W)", "duplicate_119": True,
        "near_village": True, "permitted_burn": False,
        "raw_report": "박민수(010-5555-7777)입니다. 바람이 너무 강하게 부는데 산 쪽에서 불티가 날립니다!"
    },
    "④ 해외 수출 시연 (US California)": {
        "event_id": "EV-US-2026-09", "region": "US", "location": "Los Angeles Foothills, CA",
        "lat": 34.0750, "lon": -118.2300, "cctv_smoke": True, "humidity": 15,
        "wind_speed": 8.4, "wind_dir": "Santa Ana (NE)", "duplicate_119": True,
        "near_village": True, "permitted_burn": False,
        "raw_report": "Caller John Doe (213-555-0199) reporting heavy smoke and flames near residential hillside."
    },
    "⑤ 1단계 조건부 정찰 (중간 위험)": {
        "event_id": "EV-2026-104", "region": "KR", "location": "전남 임도 인접 구역",
        "lat": 35.0050, "lon": 127.4700, "cctv_smoke": True, "humidity": 38,
        "wind_speed": 5.5, "wind_dir": "서풍(W)", "duplicate_119": False,
        "near_village": True, "permitted_burn": False,
        "raw_report": "최지은(010-2468-1357)입니다. 임도 쪽에서 옅은 연기가 보이는데 불인지 잘 모르겠어요."
    }
}

STAGE_LABELS = {
    "FALSE_ALARM": "오경보 필터링 (출동 보류)",
    "WIND_GUARDRAIL_BLOCKED": "강풍 가드레일 (이륙 차단)",
    "STAGE_1_RECON": "1단계: 비무장 정찰 조건부 출격",
    "STAGE_2_SUPPRESSION": "2단계: 지휘관 승인 필요",
}

# ==========================================
# 3. 핵심 엔진 (PII 마스킹, 거리 계산, 규칙 기반 위험도 & 가드레일)
# ==========================================
def mask_pii(text: str) -> str:
    """119/911 신고 원문에서 전화번호 및 이름 패턴 자동 비식별화"""
    # 한국/미국 전화번호 마스킹
    text = re.sub(r'(\d{2,3})[-.\s]?(\d{3,4})[-.\s]?(\d{4})', r'\1-****-\3', text)
    # 한국 이름 패턴 ("홍길동(" 형태) 및 영문 "Caller Name (" 형태 마스킹
    # 주의: "[신고자](" 형태는 화면에서 마크다운 링크로 렌더링되어 마스킹 결과가 가려지므로 공백을 둠
    text = re.sub(r'([가-힣]{2,4})\s*\(', '[신고자] (', text)
    text = re.sub(r'Caller\s+[A-Za-z]+\s+[A-Za-z]+', 'Caller [REDACTED]', text)
    return text

def haversine_km(lat1, lon1, lat2, lon2) -> float:
    """두 위경도 좌표 간 직선 거리(km) 계산"""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def evaluate_fire_risk(event: dict, wind_limit: float = 12.0) -> dict:
    """LLM 환각을 막기 위한 투명한 규칙 기반 위험도(0~100) 및 강풍 가드레일 산출"""
    score = 0
    reasons = []

    if event["cctv_smoke"]:
        score += 35
        reasons.append("CCTV 연기/화염 패턴 감지 (+35)")
    if event["humidity"] < 30:
        score += 20
        reasons.append(f"실효습도 건조 위험 {event['humidity']}% < 30% (+20)")
    if event["wind_speed"] >= 5.0:
        score += 15
        reasons.append(f"확산 촉진 풍속 {event['wind_speed']}m/s ≥ 5m/s (+15)")
    if event["duplicate_119"]:
        score += 20
        reasons.append("119/911 중복 신고 접수 (+20)")
    if event["near_village"]:
        score += 10
        reasons.append("산림 인접 민가 1km 이내 위치 (+10)")
    if event["permitted_burn"]:
        score -= 50
        reasons.append("사전 허가된 영농 소각 일정 일치 (-50)")

    score = max(0, min(100, score))
    # 가드레일은 평균풍속과 순간 최대풍속(돌풍) 중 큰 값으로 판단 (돌풍·능선 난류 대비)
    guard_wind = max(event["wind_speed"], event.get("wind_gust") or 0.0)
    wind_blocked = guard_wind > wind_limit
    wind_caution = (not wind_blocked) and guard_wind > WIND_DOCK_LIMIT

    if score < 50:
        stage = "FALSE_ALARM"
    elif wind_blocked:
        stage = "WIND_GUARDRAIL_BLOCKED"
    elif score < 80:
        stage = "STAGE_1_RECON"
    else:
        stage = "STAGE_2_SUPPRESSION"

    return {
        "score": score,
        "reasons": reasons,
        "wind_blocked": wind_blocked,
        "wind_caution": wind_caution,
        "guard_wind": guard_wind,
        "stage": stage
    }

DRONE_RANGE_KM = 20.0  # 거점 드론 작전 반경 [시뮬레이션 가정값]
# 드론 풍속 기준 [출처: 팀 자료조사 - DJI 공식 제원/매뉴얼]
#  - DJI Matrice 350 RTK 최대 풍속 저항 12m/s, Matrice 30T 이착륙 12m/s (비행 중 15m/s)
#  - DJI Dock 2 + Matrice 3D: 도크 주변 풍속 8m/s 초과 시 자동 이착륙 불가
#  - 국립해양측위정보원 무인비행장치 운용지침 제16조: 풍속 10m/s 이상 시 비행 제한 가능
WIND_HARD_LIMIT = 12.0   # 절대 중단 상한 (가드레일)
WIND_DOCK_LIMIT = 8.0    # 도크 자동 이착륙 한계 (주의 구간 시작)
WIND_SOURCE = "DJI M350 RTK 제원 12m/s · Dock 2 매뉴얼 이착륙 8m/s"
# 소방 헬기 평균 현장 도착 시간 [출처: 소방청 보도자료, 2026.08 / 팀 자료조사]
HELI_AVG_MIN = 10.3
HELI_SOURCE = "소방청 보도자료('26.08)"

def find_nearest_station(lat, lon, region):
    """동일 권역 내 최단 거리 거점 드론 스테이션 매칭 및 예상 도착 시간(가정값) 계산"""
    candidates = DRONE_STATIONS[DRONE_STATIONS["region"] == region].copy()
    if candidates.empty:
        candidates = DRONE_STATIONS.copy()
    candidates["dist_km"] = candidates.apply(lambda r: haversine_km(lat, lon, r["lat"], r["lon"]), axis=1)
    best = candidates.sort_values("dist_km").iloc[0]
    # 드론 평균 순항 속도 60km/h(1km/분) + 이륙 준비 1분 가정 [시뮬레이션 가정값]
    eta_min = round(best["dist_km"] / 1.0 + 1.0, 1)
    return best, eta_min

# ==========================================
# 4. IBM watsonx.ai 브리핑 생성 (실제 API + 오프라인 백업)
# ==========================================
@st.cache_data(show_spinner="IBM watsonx.ai 브리핑 생성 중...", ttl=3600)
def _call_watsonx(prompt, model_id, api_key, project_id, url):
    """동일 입력은 캐시하여 버튼 클릭 등 화면 재실행 시 API 중복 호출 방지"""
    credentials = Credentials(url=url, api_key=api_key)
    model = ModelInference(
        model_id=model_id,
        params={"decoding_method": "greedy", "max_new_tokens": 450, "repetition_penalty": 1.05},
        credentials=credentials,
        project_id=project_id
    )
    return model.generate_text(prompt=prompt)

# 관할 기관 (해외 수출 모드: [Jurisdiction: US-CA])
JURISDICTION = {
    "KR": {"code": "KR", "agency_kr": "산림청", "agency_en": "local Fire HQ and Korea Forest Service"},
    "US": {"code": "US-CA", "agency_kr": "CAL FIRE·LA 카운티 소방국(LACoFD)",
           "agency_en": "CAL FIRE and Los Angeles County Fire Department"},
}

def jurisdiction(event):
    return JURISDICTION.get(event["region"], JURISDICTION["KR"])

def generate_watsonx_briefing(event, risk, station, eta_min, lang, api_key, project_id, url,
                              wind_limit=12.0, model_id="ibm/granite-4-h-small"):
    prompt = f"""
You are 'K-Wildfire Shield', a vertical AI disaster command agent for fire departments.
Never invent probability scores. Use ONLY the verified rule-engine data below.
Output language: {'Korean' if lang == 'KR' else 'English'}.
[Jurisdiction: {jurisdiction(event)['code']}] Coordinate with: {jurisdiction(event)['agency_en']}.

[Verified Incident Data]
- Location: {event['location']} (Lat: {event['lat']}, Lon: {event['lon']})
- Weather: Humidity {event['humidity']}%, Wind {event['wind_speed']} m/s, max gust {event.get('wind_gust') or '-'} m/s ({event['wind_dir']})
- Rule-Based Risk Score: {risk['score']}/100 (Stage: {risk['stage']})
- Wind Guardrail Blocked: {risk['wind_blocked']} (Drone safe-flight wind limit: {wind_limit} m/s)
- Nearest Drone Station: {station['name']} ({round(station['dist_km'], 2)} km away, Est. ETA: {eta_min} min [simulation assumption])
- Benchmark: Fire helicopter average arrival {HELI_AVG_MIN} min (Source: Korea National Fire Agency press release, Aug 2026)
- Masked Caller Report: {mask_pii(event['raw_report'])}

[Governance Policy - must be followed]
- FALSE_ALARM (<50): no drone dispatch, keep CCTV monitoring, no evacuation SMS.
- STAGE_1_RECON (50-79): unarmed thermal recon drone only, inside pre-approved airspace; commander may abort. No suppression payload, no evacuation SMS without approval.
- STAGE_2_SUPPRESSION (80+): suppression payload and evacuation SMS require the commander's final approval. Write the plan as a recommendation awaiting approval.
- WIND_GUARDRAIL_BLOCKED (wind > limit, even if score >= 80): state "Wind safety guardrail active - drone launch automatically blocked"; secure detour routes for ground fire engines, pre-emptively evacuate downwind villages, switch to high-altitude recon (helicopter, fixed-wing, satellite) or ground observation teams.
- 2026 government evacuation standard: areas the fire can reach within 5 hours -> immediate evacuation order; within 8 hours -> evacuation preparation notice.
- Evacuation messages must never tell residents to move to higher ground or toward the downwind direction.
- Role scope: the fire department handles initial recon and protection of people and homes near the forest; main forest suppression is coordinated with the Korea Forest Service. Never claim drones extinguish large wildfires.

Provide a concise, structured operational briefing with 3 sections:
1. Situation Assessment (Why this score/stage was triggered)
2. Tactical Action Plan (Drone deployment or ground fallback if wind-blocked, focusing on residential defense)
3. Resident Evacuation Alert Draft (ready for SMS, applying the 5-hour / 8-hour standard, marked as sent only after commander approval)
"""
    # 실제 IBM watsonx.ai API 호출 시도
    if WATSONX_AVAILABLE and api_key and project_id:
        try:
            return _call_watsonx(prompt, model_id, api_key, project_id, url), f"IBM watsonx.ai Live ({model_id})"
        except Exception as e:
            st.warning(f"watsonx.ai 호출 오류로 안전 백업 모드로 전환합니다: {e}")

    # 오프라인/데모 백업 응답 (발표 현장 네트워크 장애 완벽 대비)
    if lang == "KR":
        if risk["stage"] == "FALSE_ALARM":
            text = (
                f"**1. 상황 판정 (오경보 필터링):** 규칙 기반 위험 점수 **{risk['score']}점**으로, "
                + ("사전 허가된 영농 소각 일정과 일치하여 산불 확산 위험이 낮습니다.\n\n" if event["permitted_burn"]
                   else "출동 기준(50점)에 미달하여 산불 확정 근거가 부족합니다. 추가 신고·CCTV 변화를 모니터링합니다.\n\n")
                + f"**2. 전술 권고:** 불필요한 거점 드론 출동을 보류하고 CCTV 모니터링만 유지하여 행정력 낭비를 방지합니다.\n\n"
                f"**3. 주민 안내:** 별도 대피 문자 발송 불필요 (정상 모니터링 유지)."
            )
        elif risk["stage"] == "WIND_GUARDRAIL_BLOCKED":
            text = (
                f"**1. 상황 판정 (강풍 가드레일 작동):** 위험 점수 **{risk['score']}점**의 고위험 발화이나, 현재 풍속(돌풍 포함) **{risk['guard_wind']}m/s**가 드론 안전 비행 임계치({wind_limit}m/s)를 초과했습니다.\n\n"
                f"**2. 전술 권고 (운영 안정성 확보):** 드론 추락 및 2차 사고 방지를 위해 **이륙을 자동 차단**합니다. 즉시 지상 소방차 우회 진입 경로를 확보하고, 풍하({event['wind_dir']}) 방향 민가 선제 대피와 {jurisdiction(event)['agency_kr']} 공조를 요청하세요. 정찰은 **고고도 정찰(헬기·고정익·위성) 또는 지상 관측팀**으로 전환합니다.\n\n"
                f"**3. 주민 대피 문자 초안 (지휘관 승인 후 발송):** `[긴급재난] {event['location']} 인근 강풍 동반 산불 의심. 5시간 이내 화재 도달 가능 지역은 즉시 지정 대피소로 대피하시고, 8시간 이내 도달 가능 지역은 대피를 준비해 주시기 바랍니다.`"
            )
        elif risk["stage"] == "STAGE_1_RECON":
            text = (
                f"**1. 상황 판정 (1단계 확인 정찰):** 규칙 기반 위험 점수 **{risk['score']}점**으로 발화 가능성은 있으나 확정 근거(중복 신고 등)가 부족합니다.\n\n"
                f"**2. 전술 권고:** 정책에 따라 최단 거점 **{station['name']}**({round(station['dist_km'],2)}km)에서 **비무장 열화상 정찰 드론**을 사전 승인 공역 내 조건부 출격합니다. 약 **{eta_min}분 내 도착**(가정값) 후 열화상으로 발화 여부를 확인하며, 지휘관은 언제든 정찰을 중단할 수 있습니다. 소화탄·지연제는 탑재하지 않습니다.\n\n"
                f"**3. 주민 안내:** 발화 확인 전이므로 대피 문자는 보류합니다. 정찰 결과 화점이 확인되면 2단계로 격상해 지휘관 승인 후 발송합니다."
            )
        else:
            text = (
                f"**1. 상황 판정 (2단계 고위험):** 규칙 기반 위험 점수 **{risk['score']}점**으로 건조·바람 조건 속 비인가 발화가 강력히 의심됩니다.\n\n"
                f"**2. 전술 권고 (지휘관 승인 대기):** 최단 거점 **{station['name']}**({round(station['dist_km'],2)}km) 드론을 투입하면 **약 {eta_min}분 내 도착**(가정값)해 소방청 헬기 평균({HELI_AVG_MIN}분, {HELI_SOURCE})보다 먼저 현장을 확인할 수 있습니다. 열화상으로 화선을 실시간 파악하고, 승인 시 인접 민가 방향 지연제 살포로 인명·민가 방어선을 구축합니다. 산림 본진 진화는 {jurisdiction(event)['agency_kr']} 측과 공조합니다.\n\n"
                f"**3. 주민 대피 문자 초안 (승인 후 발송):** `[산불경보] {event['location']} 산불 발생 추정. 5시간 이내 화재 도달 가능 지역은 즉시 안전한 곳으로 대피하시고, 8시간 이내 도달 가능 지역은 대피를 준비해 주시기 바랍니다. 소방 드론과 진화대가 출동 중입니다.`"
            )
    else:
        if risk["stage"] == "FALSE_ALARM":
            text = (
                f"**1. Situation Assessment (False Alarm Filtered):** Rule-based risk score **{risk['score']}/100**. "
                + ("The smoke matches a pre-permitted agricultural burn, so spread risk is low.\n\n" if event["permitted_burn"]
                   else "Below the dispatch threshold (50); evidence of a wildfire is insufficient. Keep monitoring for new reports.\n\n")
                + f"**2. Tactical Action Plan:** Hold drone dispatch and keep CCTV monitoring to avoid wasting response resources.\n\n"
                f"**3. Resident Notice:** No evacuation alert required (normal monitoring)."
            )
        elif risk["stage"] == "WIND_GUARDRAIL_BLOCKED":
            text = (
                f"**1. Situation Assessment (Wind Guardrail Active):** High risk (**{risk['score']}/100**), but wind speed incl. gusts (**{risk['guard_wind']} m/s**) exceeds the safe drone flight limit ({wind_limit} m/s).\n\n"
                f"**2. Tactical Action Plan:** Autonomous drone launch **BLOCKED** for operational safety. Secure detour routes for ground engines, pre-emptively evacuate homes downwind ({event['wind_dir']}), and switch to high-altitude recon (helicopter, fixed-wing, satellite). Coordinate with {jurisdiction(event)['agency_en']}.\n\n"
                f"**3. Evacuation Alert Draft (sent after commander approval):** `[EMERGENCY] Wildfire suspected near {event['location']} under high winds. Areas the fire can reach within 5 hours: evacuate now. Within 8 hours: prepare to evacuate.`"
            )
        elif risk["stage"] == "STAGE_1_RECON":
            text = (
                f"**1. Situation Assessment (Stage 1 Recon):** Verified risk score **{risk['score']}/100**. Ignition is possible but not yet confirmed.\n\n"
                f"**2. Tactical Action Plan:** Per policy, an **unarmed thermal recon drone** launches from **{station['name']}** ({round(station['dist_km'],2)} km, Est. ETA **{eta_min} min** [Simulation Assumption]) inside pre-approved airspace. The commander can abort at any time. No suppression payload.\n\n"
                f"**3. Resident Notice:** Evacuation alert on hold until the hotspot is confirmed and the commander approves Stage 2."
            )
        else:
            text = (
                f"**1. Situation Assessment:** Verified risk score **{risk['score']}/100** driven by low humidity ({event['humidity']}%) and smoke detection near residential zones.\n\n"
                f"**2. Tactical Action Plan:** Deploy drone from **{station['name']}** ({round(station['dist_km'],2)} km, Est. ETA **{eta_min} min** [Simulation Assumption]) for thermal recon; retardant line for residential perimeter defense **pending commander approval**. Inter-agency coordination with {jurisdiction(event)['agency_en']}.\n\n"
                f"**3. Evacuation Alert Draft (sent after approval):** `[WILDFIRE ALERT] Smoke and flames reported near {event['location']}. Areas the fire can reach within 5 hours: evacuate now. Within 8 hours: prepare to evacuate. Follow {jurisdiction(event)['agency_en']} instructions.`"
            )

    if station["dist_km"] > DRONE_RANGE_KM and risk["stage"] in ("STAGE_1_RECON", "STAGE_2_SUPPRESSION"):
        text += ("\n\n**⚠️ 작전 반경 경고:** 최근접 거점이 드론 작전 반경(가정값 "
                 f"{DRONE_RANGE_KM:.0f}km) 밖이므로 지상·헬기 대응을 우선하고, 해당 지역을 신규 거점 배치 후보로 보고합니다."
                 if lang == "KR" else
                 "\n\n**⚠️ Range Warning:** The nearest station is outside the drone operating radius "
                 f"({DRONE_RANGE_KM:.0f} km, assumption). Prioritize ground/air units and flag this area for a new base station.")
    return text, "Demo Fallback Mode (Rule-Grounded)"

# ==========================================
# 5. 사이드바 UI (언어 전환, 데이터 입력, IBM 설정)
# ==========================================
with st.sidebar:
    st.title("🛡️ K-Wildfire Shield")
    st.caption("IBM watsonx 기반 산불 초동 정찰·방어 에이전트")

    lang = st.radio("🌐 출력 언어 / Global Export Mode", ["KR", "EN"], horizontal=True)
    wind_limit = st.slider("💨 드론 강풍 안전 임계치 (m/s)", 8.0, 16.0, WIND_HARD_LIMIT, 0.5,
                           help=f"근거: {WIND_SOURCE}. 12m/s는 '안전 보장값'이 아니라 절대 중단 상한이며, "
                                f"{WIND_DOCK_LIMIT:.0f}m/s 초과부터 주의 구간으로 표시합니다.")

    st.divider()
    st.subheader("📂 1. 데이터 입력 (시나리오 / CSV)")
    selected_name = st.selectbox("내장 시연 시나리오 선택", list(SAMPLE_SCENARIOS.keys()))
    uploaded_csv = st.file_uploader("또는 외부/해외 데이터 CSV 업로드", type=["csv"])

    st.divider()
    with st.expander("🔑 IBM watsonx.ai API 연동 (선택)"):
        wx_url = st.text_input("Endpoint URL", value="https://us-south.ml.cloud.ibm.com")
        wx_api_key = st.text_input("API Key", type="password")
        wx_project_id = st.text_input("Project ID")
        wx_model_id = st.text_input("Model ID", value="ibm/granite-4-h-small",
                                    help="기본값 Granite 4 H-Small: 종량제 Granite 4 중 최저 단가(입력 $0.0636/출력 $0.265, 100만 토큰당). 프로젝트에서 쓸 수 있는 다른 모델 ID로 바꿀 수 있습니다.")
        if not WATSONX_AVAILABLE:
            st.caption("⚠️ ibm-watsonx-ai 패키지 미설치: Demo Fallback 모드로 동작합니다.")

# 업로드된 CSV가 있으면 선택한 행을 이벤트로 변환, 아니면 선택 시나리오 사용
# 한글 컬럼(팀 자료조사 korea_wildfire_weather.csv 등) → 내부 표준 컬럼 매핑
COLUMN_ALIASES = {
    "날짜": "date", "일자": "date",
    "지역": "location", "시군구": "location",
    "위도": "lat", "latitude": "lat", "경도": "lon", "longitude": "lon", "lng": "lon",
    "평균습도": "humidity", "습도": "humidity", "상대습도": "humidity", "실효습도": "humidity",
    "최소습도": "humidity_min",
    "평균풍속": "wind_speed", "풍속": "wind_speed",
    "최대풍속": "wind_gust", "최대순간풍속": "wind_gust",
    "풍향": "wind_dir", "관측소": "station_obs",
    "화재여부": "fire_label", "산불여부": "fire_label", "fire": "fire_label",
    "cctv연기": "cctv_smoke", "중복신고": "duplicate_119", "민가인접": "near_village", "허가소각": "permitted_burn",
    "신고내용": "raw_report", "사건id": "event_id", "권역": "region",
}

@st.cache_data(show_spinner=False)
def load_csv(file_bytes: bytes) -> pd.DataFrame:
    import io
    for enc in ("utf-8-sig", "cp949"):  # BOM 포함 UTF-8, 공공데이터 한글 CSV(cp949) 대비
        try:
            df = pd.read_csv(io.BytesIO(file_bytes), encoding=enc)
            break
        except UnicodeDecodeError:
            continue
    df.columns = [str(c).strip() for c in df.columns]
    df = df.rename(columns=lambda c: COLUMN_ALIASES.get(c, COLUMN_ALIASES.get(c.lower(), c.lower())))
    return df

df_up = None
if uploaded_csv is not None:
    df_up = load_csv(uploaded_csv.getvalue())
    if df_up.empty or "lat" not in df_up.columns or "lon" not in df_up.columns:
        st.error("CSV에 데이터가 없거나 위도/경도(lat/lon) 컬럼이 없습니다.")
        st.stop()
    st.sidebar.success(f"CSV 로드 완료 ({len(df_up):,}건)")

    view = df_up
    if "fire_label" in df_up.columns:
        pick = st.sidebar.radio("행 필터 (화재여부)", ["산불 발생(1)", "미발생(0)", "전체"], horizontal=True)
        if pick != "전체":
            view = df_up[df_up["fire_label"] == (1 if pick.startswith("산불") else 0)]
    def _label(i):
        r = view.loc[i]
        parts = [str(r.get(k)) for k in ("date", "location") if k in view.columns]
        return f"#{i} " + " · ".join(parts)
    row_idx = st.sidebar.selectbox("분석할 행", view.index.tolist(), format_func=_label)
    row = df_up.loc[row_idx]

    # CSV에 없는 현장 신호는 시연용 가정값으로 입력 (기상 데이터에는 CCTV·신고 정보가 없음)
    missing = [k for k in ("cctv_smoke", "duplicate_119", "near_village", "permitted_burn") if k not in df_up.columns]
    sim = {}
    if missing:
        with st.sidebar.expander("🧪 CSV에 없는 현장 신호 (시연용 가정)", expanded=False):
            labels = {"cctv_smoke": ("CCTV 연기 감지", True), "duplicate_119": ("119 중복 신고", False),
                      "near_village": ("민가 1km 이내", True), "permitted_burn": ("허가된 영농 소각", False)}
            for k in missing:
                sim[k] = st.checkbox(labels[k][0], value=labels[k][1], key=f"sim_{k}")

    def _s(key, default):
        v = row.get(key, default)
        return default if (v is None or (isinstance(v, float) and math.isnan(v))) else str(v)
    lat, lon = to_float(row.get("lat"), 34.0750), to_float(row.get("lon"), -118.2300)
    in_korea = 33.0 <= lat <= 39.0 and 124.0 <= lon <= 132.0
    region = _s("region", "KR" if in_korea else "US").upper()
    loc = _s("location", "Uploaded Region")
    obs = _s("station_obs", "")
    default_report = (f"{_s('date', '')} {loc} 산불 의심 신고 / 기상청 {obs} 관측소 기상 데이터 기반" if region == "KR"
                      else f"Smoke report near {loc} (weather-station data)")
    gust = to_float(row.get("wind_gust"), float("nan"))
    event = {
        "event_id": _s("event_id", f"CSV-{_s('date', '').replace('-', '')}-{row_idx}"),
        "region": region,
        "location": loc,
        "lat": lat, "lon": lon,
        "cctv_smoke": to_bool(row.get("cctv_smoke"), sim.get("cctv_smoke", True)),
        "humidity": to_float(row.get("humidity"), 20.0),
        "wind_speed": to_float(row.get("wind_speed"), 3.0),
        "wind_gust": None if math.isnan(gust) else gust,
        "wind_dir": _s("wind_dir", "-"),
        "duplicate_119": to_bool(row.get("duplicate_119"), sim.get("duplicate_119", False)),
        "near_village": to_bool(row.get("near_village"), sim.get("near_village", True)),
        "permitted_burn": to_bool(row.get("permitted_burn"), sim.get("permitted_burn", False)),
        "raw_report": _s("raw_report", default_report),
        "fire_label": None if "fire_label" not in df_up.columns or pd.isna(row.get("fire_label")) else int(row.get("fire_label")),
    }
else:
    event = SAMPLE_SCENARIOS[selected_name]

# ==========================================
# 6. 메인 대시보드 실행
# ==========================================
st.header("🔥 K-Wildfire Shield: 수직적 산불 대응 AI 지휘 대시보드" if lang == "KR" else "🔥 K-Wildfire Shield: Vertical Wildfire Command Agent")

risk = evaluate_fire_risk(event, wind_limit)
station, eta_min = find_nearest_station(event["lat"], event["lon"], event["region"])
out_of_range = station["dist_km"] > DRONE_RANGE_KM
masked_report = mask_pii(event["raw_report"])

# 상단 KPI 지표 (시뮬레이션 가정값 명시)
c1, c2, c3, c4 = st.columns(4)
c1.metric("규칙 기반 화재 위험 점수", f"{risk['score']} / 100")
gust_txt = f" (돌풍 {event['wind_gust']})" if event.get("wind_gust") is not None else ""
if risk["wind_blocked"]:
    wind_delta, wind_color = f"강풍 차단! {risk['guard_wind']} > {wind_limit}m/s", "inverse"
elif risk["wind_caution"]:
    wind_delta, wind_color = f"주의: 도크 자동 이착륙 한계 {WIND_DOCK_LIMIT:.0f}m/s 초과", "off"
else:
    wind_delta, wind_color = "비행 안전", "normal"
c2.metric(f"현재 풍속{gust_txt} (임계치 비교)", f"{event['wind_speed']} m/s", delta=wind_delta, delta_color=wind_color)
if risk["stage"] == "FALSE_ALARM":
    c3.metric("거점 드론 예상 도착 (가정값)", "출동 불필요", delta="행정력 낭비 방지", delta_color="off")
elif risk["wind_blocked"]:
    c3.metric("거점 드론 예상 도착 (가정값)", "출동 차단", delta="지상 대응 전환", delta_color="inverse")
elif out_of_range:
    c3.metric("거점 드론 예상 도착 (가정값)", "작전 반경 밖",
              delta=f"최근접 거점 {round(station['dist_km'], 1)}km > {DRONE_RANGE_KM:.0f}km", delta_color="inverse")
else:
    saved = round(HELI_AVG_MIN - eta_min, 1)
    c3.metric("거점 드론 예상 도착 (가정값)", f"{eta_min} 분",
              delta=f"소방청 헬기 평균({HELI_AVG_MIN}분, '26.08) 대비 {saved}분 단축" if saved > 0 else "헬기 평균 대비 단축 없음",
              delta_color="normal" if saved > 0 else "off")
c4.metric("거버넌스 대응 단계", risk["stage"])
c4.caption(STAGE_LABELS[risk["stage"]])
st.caption(f"※ 헬기 평균 도착 {HELI_AVG_MIN}분 출처: {HELI_SOURCE}. 풍속 기준 출처: {WIND_SOURCE}. "
           "드론 도착 시간·작전 반경·확산 반경은 시뮬레이션 가정값입니다.")
if event.get("fire_label") is not None:
    actual = "산불 발생(1)" if event["fire_label"] == 1 else "미발생(0)"
    st.caption(f"📌 CSV 실제 기록: **{actual}** · 규칙 엔진 판정: **{risk['stage']}** "
               "(CCTV·신고·민가 신호는 시연용 가정값)")

# 업로드 데이터로 규칙 엔진 임계치 검증 (LLM 아닌 실제 데이터로 기준의 타당성 확인)
if df_up is not None and "fire_label" in df_up.columns:
    with st.expander("📊 업로드 데이터로 규칙 엔진 기준 검증 (습도·풍속 조건별 실제 산불 비율)", expanded=False):
        d = df_up.dropna(subset=["fire_label"])
        rows = []
        checks = [("humidity", "<", 30, "평균습도 < 30% (+20 기준)"), ("humidity_min", "<", 30, "최소습도 < 30%"),
                  ("wind_speed", ">=", 5, "평균풍속 ≥ 5m/s (+15 기준)"), ("wind_gust", ">=", 5, "최대풍속 ≥ 5m/s")]
        for col, op, th, name in checks:
            if col not in d.columns:
                continue
            dd = d.dropna(subset=[col])
            m = dd[col] < th if op == "<" else dd[col] >= th
            yes, no = dd[m]["fire_label"].mean(), dd[~m]["fire_label"].mean()
            rows.append({"조건": name, "해당 행 수": int(m.sum()), "해당 시 산불 비율": f"{yes:.1%}",
                         "비해당 시 산불 비율": f"{no:.1%}", "배율": f"{yes / no:.1f}배" if no else "-"})
        st.dataframe(pd.DataFrame(rows), hide_index=True, **wide_kwargs())
        st.caption(f"전체 {len(d):,}행 중 산불 발생 {int(d['fire_label'].sum()):,}행. 산불 발생일과 비교일을 골라 만든 표본이므로 "
                   "비율은 실제 발생 확률이 아니라 조건 간 상대 비교용입니다.")

        fires = d[d["fire_label"] == 1]
        kr_st = DRONE_STATIONS[DRONE_STATIONS["region"] == "KR"]
        if not fires.empty and not kr_st.empty:
            near = fires.apply(lambda r: min(haversine_km(r["lat"], r["lon"], s2["lat"], s2["lon"])
                                             for _, s2 in kr_st.iterrows()) <= DRONE_RANGE_KM, axis=1)
            st.markdown(f"**현재 거점 커버리지:** 산불 발생 지점 {len(fires):,}곳 중 {near.mean():.1%}만 "
                        f"기존 거점 {DRONE_RANGE_KM:.0f}km 반경 안에 있습니다.")
            if "location" in fires.columns:
                top = fires.groupby("location").agg(산불건수=("fire_label", "size"), 위도=("lat", "mean"), 경도=("lon", "mean"))
                top = top.sort_values("산불건수", ascending=False).head(5).round(4).reset_index().rename(columns={"location": "지역"})
                st.markdown("**신규 거점 배치 후보 (산불 다발 지역 Top 5)**")
                st.dataframe(top, hide_index=True, **wide_kwargs())


st.divider()

# 좌측: 보안 비식별화 + 지도 / 우측: watsonx 브리핑 + 지휘관 승인
col_left, col_right = st.columns([1.15, 1])

with col_left:
    st.subheader("🔒 1단계: 신고 수신 및 개인정보 자동 비식별화 (PII Masking)")
    st.caption("📡 다중 탐지 입력: AI CCTV · 기상청 다중 위성(천리안 2A호 등) · 119/911 신고 통합 수신")
    # RBAC: 119 내부망의 인가된 지휘관만 원문 열람 가능, 열람 시 감사 로그 기록
    rbac_view = st.checkbox("🔐 인가된 지휘관 원문 열람 (RBAC · 119 내부망 전용)", key=f"rbac_{event['event_id']}",
                            help="역전화 확인용. 원문은 AI(외부 LLM)로 전송되지 않고, 열람 기록은 감사 로그에 남습니다.")
    if rbac_view:
        st.text(f"[원문 · 내부망 전용] {event['raw_report']}")
        view_key = f"view|{event['event_id']}"
        if view_key not in st.session_state.auto_launched:
            st.session_state.auto_launched.add(view_key)
            st.session_state.audit_logs.append({
                "시각": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"), "사건ID": event["event_id"],
                "위험점수": risk["score"], "판정단계": risk["stage"], "풍속(m/s)": event["wind_speed"], "투입거점": "-",
                "결정": "신고자 원문 열람 (RBAC)", "결정주체": "인가 지휘관",
            })
    else:
        st.caption("신고자 원문은 권한 있는 지휘관만 열람할 수 있습니다. AI에는 아래 비식별화 결과만 전송됩니다.")
    st.success("[AI 전송용 비식별화 완료] " + masked_report.replace("[", "\\[").replace("*", "\\*"))

    st.subheader("🗺️ 2단계: 발화점 · 거점 드론 최적 경로 시각화")
    m = folium.Map(location=[event["lat"], event["lon"]], zoom_start=12)

    # 발화점 및 확산 예상 반경
    folium.Circle(
        location=[event["lat"], event["lon"]], radius=800,
        color="red", fill=True, fill_opacity=0.25, popup="확산 경계 반경 (시뮬레이션)"
    ).add_to(m)
    folium.Marker(
        [event["lat"], event["lon"]],
        popup=f"발화 의심: {event['location']}", icon=folium.Icon(color="red", icon="fire")
    ).add_to(m)

    # 거점 드론 스테이션 표시
    for _, st_row in DRONE_STATIONS[DRONE_STATIONS["region"] == event["region"]].iterrows():
        is_best = st_row["id"] == station["id"]
        folium.Marker(
            [st_row["lat"], st_row["lon"]],
            popup=f"{st_row['name']} (배터리 {st_row['battery']}%)",
            icon=folium.Icon(color="blue" if is_best else "gray", icon="plane")
        ).add_to(m)

    # 비행 가능 시 최적 출동 경로선 표시
    if risk["stage"] in ["STAGE_1_RECON", "STAGE_2_SUPPRESSION"] and not out_of_range:
        folium.PolyLine(
            [[station["lat"], station["lon"]], [event["lat"], event["lon"]]],
            color="blue", weight=4, dash_array="5"
        ).add_to(m)

    render_html(m.get_root().render(), height=380)

with col_right:
    st.subheader("🤖 3단계: IBM watsonx.ai 전술 브리핑 & 판단 근거")
    with st.expander("투명한 규칙 엔진 채점 근거 보기 (환각 방지)", expanded=True):
        for r in risk["reasons"]:
            st.write(f"- {r}")

    briefing_text, engine_used = generate_watsonx_briefing(
        event, risk, station, eta_min, lang, wx_api_key, wx_project_id, wx_url,
        wind_limit=wind_limit, model_id=wx_model_id
    )
    st.caption(f"Engine: `{engine_used}`")
    st.info(briefing_text)

    st.subheader("⚖️ 4단계: 2단계 거버넌스 지휘관 최종 승인")
    commander_id = st.text_input("승인 지휘관 ID", value="CMD-FIRE-01")
    stage = risk["stage"]

    def add_log(decision, actor, deployed):
        st.session_state.audit_logs.append({
            "시각": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
            "사건ID": event["event_id"],
            "위험점수": risk["score"],
            "판정단계": stage,
            "풍속(m/s)": event["wind_speed"],
            "투입거점": deployed,
            "결정": decision,
            "결정주체": actor,
        })

    if stage == "FALSE_ALARM":
        st.caption("오경보로 판정되어 드론 출동 승인 대상이 아닙니다. 모니터링 유지 여부만 기록합니다.")
        approve_label, approve_disabled = "✅ 지휘관 최종 승인 (출동/전파 실행)", True
        hold_label = "📝 오경보 확인 · 모니터링 유지 기록"
    elif stage == "WIND_GUARDRAIL_BLOCKED":
        st.error(f"강풍 가드레일: 풍속(돌풍 포함) {risk['guard_wind']}m/s > 임계치 {wind_limit}m/s. 드론 이륙 승인이 시스템적으로 잠겨 있습니다.")
        approve_label, approve_disabled = "✅ 지휘관 최종 승인 (출동/전파 실행)", True
        hold_label = "🚒 지상 대응 전환 · 선제 대피 확인"
    elif stage == "STAGE_1_RECON":
        # 1단계 정책: 사전 승인 공역 내 비무장 정찰 드론은 자동 출격, 지휘관은 중단 가능
        auto_key = f"{event['event_id']}|{risk['score']}"
        if auto_key not in st.session_state.auto_launched:
            st.session_state.auto_launched.add(auto_key)
            add_log("1단계 정책에 따른 비무장 정찰 드론 자동 출격", "SYSTEM(정책)", station["name"])
        st.info("1단계 정책: 사전 승인 공역 내 비무장 열화상 정찰 드론이 자동 출격했습니다. 소화탄·대피 문자는 실행하지 않으며, 지휘관은 언제든 중단할 수 있습니다.")
        approve_label, approve_disabled = "✅ 정찰 계속 확인", False
        hold_label = "🛑 정찰 중단 (지휘관 통제)"
    else:
        st.warning("2단계 정책: 소화탄·지연제 투하와 주민 대피 문자 발송은 지휘관 최종 승인 후에만 실행됩니다.")
        approve_label, approve_disabled = "✅ 지휘관 최종 승인 (출동/전파 실행)", False
        hold_label = "🛑 출동 보류 / 지상 대응 전환"

    if out_of_range and stage in ("STAGE_1_RECON", "STAGE_2_SUPPRESSION"):
        st.warning(f"최근접 거점({station['name']})이 {round(station['dist_km'], 1)}km 떨어져 드론 작전 반경({DRONE_RANGE_KM:.0f}km, 가정값) 밖입니다. "
                   "지상 진화대·헬기 출동과 관할 기관 공조를 우선 검토하세요. (신규 거점 배치 후보 지역)")
    b1, b2 = st.columns(2)
    if b1.button(approve_label, disabled=approve_disabled, **wide_kwargs()):
        if stage == "STAGE_1_RECON":
            add_log("지휘관 정찰 계속 확인", f"지휘관 {commander_id}", station["name"])
            st.success("정찰 계속이 감사 로그에 기록되었습니다.")
        else:
            add_log("지휘관 최종 승인 (소화탄·대피 문자 실행)", f"지휘관 {commander_id}", station["name"])
            st.success("승인 완료! watsonx Orchestrate 워크플로(드론 출동·관계기관 전파·대피 문자 발송) 실행 및 감사 로그 기록됨.")

    if b2.button(hold_label, **wide_kwargs()):
        decision = {
            "FALSE_ALARM": "오경보 확인 · 모니터링 유지",
            "WIND_GUARDRAIL_BLOCKED": "강풍 차단 · 지상 대응 전환",
            "STAGE_1_RECON": "정찰 중단 (지휘관 통제)",
            "STAGE_2_SUPPRESSION": "출동 보류 (안전 통제)",
        }[stage]
        add_log(decision, f"지휘관 {commander_id}", "지상 대응 / 보류")
        st.warning(f"'{decision}'이(가) 감사 로그에 기록되었습니다.")

# ==========================================
# 7. 하단 거버넌스 감사 로그 (Audit Log)
# ==========================================
st.divider()
st.subheader("📋 watsonx.governance 실시간 감사 로그 (Audit Log)")
if st.session_state.audit_logs:
    df_log = pd.DataFrame(st.session_state.audit_logs)
    st.dataframe(df_log, **wide_kwargs())
    st.download_button(
        "📥 감사 로그 CSV 다운로드",
        df_log.to_csv(index=False).encode("utf-8-sig"),
        file_name="k_wildfire_audit_log.csv",
        mime="text/csv"
    )
else:
    st.caption("지휘관 승인 또는 보류 버튼을 누르면 실시간 감사 로그가 이곳에 기록됩니다.")