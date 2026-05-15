#!/bin/sh
# Naver 부동산 호가 분석 wrapper.
#
# Subcommands:
#   region <cortarNo>                     # 법정동 코드로 단지 리스트
#   search <단지명>                         # 단지명으로 후보 검색
#   articles <complexNo> [flags]          # 단지 매물 분석
#     flags:  --trade A1|B1|B2            # A1=매매 (default), B1=전세, B2=월세
#             --pages N                   # default 5, max 30
#             --price-min N  --price-max N   # 만원 단위
#             --area-min N   --area-max N    # 전용면적(㎡)
#             --tags 다주택급매,로열층       # 콤마구분 태그 필터
#             --include-all               # all_articles 포함 (대용량)
set -e

CMD="$1"
shift || true

: "${API_PROXY_URL:=$CORE_AGENT_API_PROXY_URL}"
if [ -z "$API_PROXY_URL" ] || [ -z "$GATEWAY_TOKEN" ]; then
  echo '{"success":false,"error":"not_configured","detail":"API_PROXY_URL or GATEWAY_TOKEN not set"}'
  exit 1
fi

HEADERS='-H "Authorization: Bearer '"$GATEWAY_TOKEN"'" -H "Content-Type: application/json"'

case "$CMD" in
  region)
    CORTAR="$1"
    if [ -z "$CORTAR" ]; then
      echo '{"success":false,"error":"usage","detail":"naver-re.sh region <cortarNo>"}'; exit 1
    fi
    curl -sS -X POST "$API_PROXY_URL/v1/naver-re/region/complexes" \
      -H "Authorization: Bearer $GATEWAY_TOKEN" \
      -H "Content-Type: application/json" \
      --max-time 30 \
      -d "{\"cortarNo\":\"$CORTAR\"}"
    ;;

  search)
    NAME="$1"
    if [ -z "$NAME" ]; then
      echo '{"success":false,"error":"usage","detail":"naver-re.sh search <단지명>"}'; exit 1
    fi
    if command -v jq >/dev/null 2>&1; then
      BODY=$(jq -cn --arg name "$NAME" '{name:$name}')
    else
      ESC=$(printf '%s' "$NAME" | sed 's/\\/\\\\/g; s/"/\\"/g')
      BODY="{\"name\":\"$ESC\"}"
    fi
    curl -sS -X POST "$API_PROXY_URL/v1/naver-re/complex/search" \
      -H "Authorization: Bearer $GATEWAY_TOKEN" \
      -H "Content-Type: application/json" \
      --max-time 30 \
      -d "$BODY"
    ;;

  articles)
    COMPLEX="$1"
    shift || true
    if [ -z "$COMPLEX" ]; then
      echo '{"success":false,"error":"usage","detail":"naver-re.sh articles <complexNo> [flags]"}'; exit 1
    fi
    TRADE="A1"
    PAGES=5
    PRICE_MIN=""
    PRICE_MAX=""
    AREA_MIN=""
    AREA_MAX=""
    TAGS=""
    INCLUDE_ALL="false"
    while [ $# -gt 0 ]; do
      case "$1" in
        --trade)      TRADE="$2"; shift 2 ;;
        --pages)      PAGES="$2"; shift 2 ;;
        --price-min)  PRICE_MIN="$2"; shift 2 ;;
        --price-max)  PRICE_MAX="$2"; shift 2 ;;
        --area-min)   AREA_MIN="$2"; shift 2 ;;
        --area-max)   AREA_MAX="$2"; shift 2 ;;
        --tags)       TAGS="$2"; shift 2 ;;
        --include-all) INCLUDE_ALL="true"; shift ;;
        *) echo '{"success":false,"error":"usage","detail":"unknown flag '"$1"'"}'; exit 1 ;;
      esac
    done

    if command -v jq >/dev/null 2>&1; then
      BODY=$(jq -cn \
        --arg complexNo "$COMPLEX" \
        --arg tradeType "$TRADE" \
        --argjson pages "$PAGES" \
        --argjson includeAll "$INCLUDE_ALL" \
        --arg priceMin "$PRICE_MIN" --arg priceMax "$PRICE_MAX" \
        --arg areaMin "$AREA_MIN" --arg areaMax "$AREA_MAX" \
        --arg tags "$TAGS" \
        '{complexNo:$complexNo, tradeType:$tradeType, pages:$pages, includeAll:$includeAll}
          + (if $priceMin!="" then {priceMin:($priceMin|tonumber)} else {} end)
          + (if $priceMax!="" then {priceMax:($priceMax|tonumber)} else {} end)
          + (if $areaMin !="" then {areaMin:($areaMin|tonumber)}  else {} end)
          + (if $areaMax !="" then {areaMax:($areaMax|tonumber)}  else {} end)
          + (if $tags    !="" then {tags:($tags|split(","))}      else {} end)')
    else
      # Fallback — no jq. Minimal body; filters become strings, server still parses.
      EXTRA=""
      [ -n "$PRICE_MIN" ] && EXTRA="$EXTRA,\"priceMin\":$PRICE_MIN"
      [ -n "$PRICE_MAX" ] && EXTRA="$EXTRA,\"priceMax\":$PRICE_MAX"
      [ -n "$AREA_MIN" ]  && EXTRA="$EXTRA,\"areaMin\":$AREA_MIN"
      [ -n "$AREA_MAX" ]  && EXTRA="$EXTRA,\"areaMax\":$AREA_MAX"
      BODY="{\"complexNo\":\"$COMPLEX\",\"tradeType\":\"$TRADE\",\"pages\":$PAGES,\"includeAll\":$INCLUDE_ALL$EXTRA}"
    fi

    curl -sS -X POST "$API_PROXY_URL/v1/naver-re/complex/articles" \
      -H "Authorization: Bearer $GATEWAY_TOKEN" \
      -H "Content-Type: application/json" \
      --max-time 40 \
      -d "$BODY"
    ;;

  *)
    echo '{"success":false,"error":"usage","detail":"naver-re.sh <region|search|articles> ..."}'
    exit 1
    ;;
esac
