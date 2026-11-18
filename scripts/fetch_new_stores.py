"""명지 지역에서 최근 2년 안에 인허가를 받은 일반음식점을 공공데이터포털에서 받아
new-stores.json 으로 저장합니다.

데이터: 행정안전부_식품_일반음식점 조회서비스 (data.go.kr 15154916)
필요한 환경 변수: DATA_GO_KR_KEY (공공데이터포털 일반 인증키, Decoding 값)
"""
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://apis.data.go.kr/1741000/general_restaurants/info"
GANGSEO = "3360000"          # 부산광역시 강서구 개방자치단체코드
YEARS = 2
OUT = os.path.join(os.path.dirname(__file__), "..", "new-stores.json")

# 업태구분명 -> 앱의 음식 종류. 목록에 없는 업태(호프, 주점, 분식, 카페 등)는 모임 장소가 아니라서 뺍니다.
CAT = {
    "한식": "한식", "냉면집": "한식", "탕류(보신용)": "한식",
    "식육(숯불구이)": "고기·구이",
    "회집": "해산물·회", "횟집": "해산물·회", "복어취급": "해산물·회",
    "중국식": "중식",
    "일식": "일식",
    "경양식": "양식", "패밀리레스트랑": "양식", "패밀리레스토랑": "양식",
    "외국음식전문점(인도,태국등)": "기타",
    "뷔페식": "뷔페",
}

# 보정계수 없는 Bessel 중부원점 TM (데이터 설명의 EPSG:5174) -> 위경도
TM = ("+proj=tmerc +lat_0=38 +lon_0=127.0028902777778 +k=1 +x_0=200000 +y_0=500000 "
      "+ellps=bessel +units=m +no_defs +towgs84=-115.80,474.99,674.11,1.16,-2.31,-1.63,6.43")


def to_latlng(x, y):
    try:
        from pyproj import Transformer
    except ImportError:
        return None
    try:
        lng, lat = Transformer.from_crs(TM, "EPSG:4326", always_xy=True).transform(float(x), float(y))
    except (TypeError, ValueError):
        return None
    # 명지 주변이 아니면 좌표를 버리고 앱이 주소로 다시 찾게 합니다.
    if not (34.9 < lat < 35.3 and 128.7 < lng < 129.1):
        return None
    return round(lat, 6), round(lng, 6)


def capacity(area):
    """시설총규모(㎡)로 단체 수용 인원을 대략 추정합니다."""
    try:
        a = float(area)
    except (TypeError, ValueError):
        return 10
    for limit, cap in ((40, 4), (66, 5), (100, 10), (130, 15), (200, 20)):
        if a < limit:
            return cap
    return 40


def get(url, tries=3):
    """포털이 가끔 응답하지 않으므로, 연결 오류와 서버 오류는 잠시 쉬었다가 다시 시도합니다."""
    for n in range(1, tries + 1):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            if e.code < 500 or n == tries:
                raise
            print("서버 오류 %d, %d초 뒤 다시 시도 (%d/%d)" % (e.code, 5 * n, n, tries))
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if n == tries:
                raise ConnectionError("공공데이터포털에 %d번 연결을 시도했지만 실패했습니다: %s" % (tries, e))
            print("연결 오류(%s), %d초 뒤 다시 시도 (%d/%d)" % (e, 5 * n, n, tries))
        time.sleep(5 * n)


PAGE = 30        # 한 번에 받는 개수. 포털이 큰 요청에서 자주 멈춰서 작게 나눕니다.
MONTHS = 2       # 인허가일 기간도 이 개월 수씩 잘라서 요청합니다.


def fetch_range(key, start, end):
    """인허가일이 start 이상, end 미만인 강서구 일반음식점을 모두 받습니다."""
    rows, page = [], 1
    while True:
        q = {
            "serviceKey": key, "pageNo": page, "numOfRows": PAGE, "returnType": "json",
            "cond[OPN_ATMY_GRP_CD::EQ]": GANGSEO,
            "cond[LCPMT_YMD::GTE]": start,
            "cond[LCPMT_YMD::LT]": end,
        }
        url = API + "?" + urllib.parse.urlencode(q)
        try:
            text = get(url)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:600]
            sys.exit("공공데이터포털이 HTTP %d 로 거부했습니다.\n응답: %s\n"
                     "인증키가 아직 활성화되지 않았거나(발급 후 최대 1시간), 이 API의 활용신청이 승인되지 않았거나, "
                     "키를 잘못 넣었을 수 있습니다." % (e.code, body))
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            sys.exit("API가 JSON이 아닌 응답을 보냈습니다: " + text[:300])
        if "response" not in data:
            sys.exit("API 오류: " + json.dumps(data, ensure_ascii=False)[:500])
        head = data["response"].get("header", {})
        if str(head.get("resultCode", "00")) not in ("00", "0"):
            sys.exit("API 오류: %s %s" % (head.get("resultCode"), head.get("resultMsg")))
        body = data["response"].get("body", {})
        items = (body.get("items") or {})
        items = items.get("item", []) if isinstance(items, dict) else items
        if isinstance(items, dict):
            items = [items]
        rows += items
        total = int(body.get("totalCount") or 0)
        if not items or len(rows) >= total:
            print("%s~%s: %d곳" % (start, end, len(rows)))
            return rows
        page += 1
        time.sleep(0.5)


def add_months(d, n):
    m = d.month - 1 + n
    return d.replace(year=d.year + m // 12, month=m % 12 + 1, day=1)


FAILED = []


def fetch(key, since):
    start = dt.datetime.strptime(since, "%Y%m%d").date()
    end_all = dt.date.today() + dt.timedelta(days=1)
    rows = []
    while start < end_all:
        end = min(add_months(start, MONTHS), end_all)
        a, b = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
        try:
            rows += fetch_range(key, a, b)
        except (ConnectionError, urllib.error.HTTPError) as e:
            # 이 기간만 건너뛰고, 받은 것은 저장합니다. 다음 실행에서 다시 받습니다.
            print("%s~%s 기간은 받지 못했습니다: %s" % (a, b, e))
            FAILED.append("%s~%s" % (a, b))
        start = end
        time.sleep(0.5)
    print("모두 %d곳 받음" % len(rows))
    return rows


def main():
    key = os.environ.get("DATA_GO_KR_KEY", "").strip()
    # Encoding 키(%2B 같은 글자가 든 키)를 넣었어도 동작하도록 한 번 풀어 줍니다.
    if "%" in key:
        key = urllib.parse.unquote(key)
    if not key:
        sys.exit("DATA_GO_KR_KEY 가 없습니다. 저장소 Settings > Secrets 에 공공데이터포털 인증키를 넣어 주세요.")
    today = dt.date.today()
    since = today.replace(year=today.year - YEARS).strftime("%Y%m%d")
    rows = fetch(key, since)

    out, skipped = [], {}
    for r in rows:
        addr_road = (r.get("ROAD_NM_ADDR") or "").strip()
        addr_lot = (r.get("LOTNO_ADDR") or "").strip()
        if "명지동" not in addr_lot and "명지" not in addr_road:
            continue
        if "영업" not in (r.get("SALS_STTS_NM") or "") or (r.get("CLSBIZ_YMD") or "").strip():
            continue
        kind = (r.get("BZSTAT_SE_NM") or r.get("SNTTN_BZSTAT_NM") or "").strip()
        if kind not in CAT:
            skipped[kind] = skipped.get(kind, 0) + 1
            continue
        ymd = (r.get("LCPMT_YMD") or "").replace("-", "")[:8]
        opened = "%s.%s.%s" % (ymd[:4], ymd[4:6], ymd[6:8]) if len(ymd) == 8 else "?"
        area = (r.get("FCLT_TOTAL_SCL") or "").strip()
        s = {
            "id": "lic-" + (r.get("MNG_NO") or r.get("BPLC_NM", "")).strip(),
            "name": (r.get("BPLC_NM") or "").strip(),
            "cat": CAT[kind],
            "cap": capacity(area),
            "park": "unknown",
            "price": None,
            "room": False,
            "addr": addr_road or addr_lot,
            "tel": (r.get("TELNO") or "").strip(),
            "memo": "%s 영업 허가 (%s). 시설 %s㎡ 기준 인원 추정, 주차·가격 미확인." % (opened, kind, area or "?"),
            "opened": ymd,
            "unverified": True,
            "source": "license",
        }
        ll = to_latlng(r.get("CRD_INFO_X"), r.get("CRD_INFO_Y"))
        if ll:
            s["lat"], s["lng"] = ll
        out.append(s)

    out.sort(key=lambda s: s["opened"], reverse=True)
    if FAILED and not rows:
        sys.exit("한 곳도 받지 못했습니다. 실패한 기간: " + ", ".join(FAILED))
    doc = {"updated": today.isoformat(), "since": since, "count": len(out), "missing": FAILED, "stores": out}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    print("명지 신규 음식점 %d곳 저장 (기준 %s 이후)" % (len(out), since))
    if FAILED:
        print("::warning::일부 기간을 받지 못했습니다(다음 실행 때 다시 받습니다): " + ", ".join(FAILED))
    if skipped:
        print("업태 때문에 뺀 곳:", json.dumps(skipped, ensure_ascii=False))


if __name__ == "__main__":
    main()
