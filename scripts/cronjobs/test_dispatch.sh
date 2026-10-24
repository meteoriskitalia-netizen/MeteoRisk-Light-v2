#!/usr/bin/env bash
# MeteoRisk Light - Test & Dispatch Tool for Forecast Workflow Cronjobs
# Usage:
#   GH_TOKEN=xxx ./test_dispatch.sh --model dwd
#   ./test_dispatch.sh --dry-run
#   ./test_dispatch.sh --list

set -euo pipefail

REPO="meteoriskitalia-netizen/MeteoRisk-Light-v2"
API_BASE="https://api.github.com/repos/${REPO}/actions/workflows"

declare -A WORKFLOWS=(
  ["dwd"]="forecast-maps-dwd.yml"
  ["dwdicon"]="forecast-maps-dwdicon.yml"
  ["meteofrance"]="forecast-maps-meteofrance.yml"
  ["ncep"]="forecast-maps-ncep.yml"
  ["ecmwf"]="forecast-maps-ecmwf.yml"
  ["index"]="forecast-maps-index.yml"
)

declare -A DESCRIPTIONS=(
  ["dwd"]="DWD ICON-EU + ICON-D2 (8 run; 2 slot/run: parziale 01:10, completo 03:00, ogni 3h UTC)"
  ["dwdicon"]="DWD ICON Global (4 run; parziale 03:01, completo 03:41, ogni 6h UTC)"
  ["meteofrance"]="Meteo-France ARPEGE 0.25 (4 run; parziale 04:20, completo 05:29, ogni 6h UTC)"
  ["ncep"]="NCEP GFS 0.25 + 0.117 (4 run; parziale 03:55, completo 05:52, ogni 6h UTC)"
  ["ecmwf"]="ECMWF IFS 0.25 (4 run; parziale 07:48, completo 09:52, ogni 6h UTC)"
  ["index"]="WebP Index Aggregator (ogni 6h :50 UTC)"
)

print_usage() {
  echo "Uso: $0 [opzioni]"
  echo "Opzioni:"
  echo "  --list             Mostra tutti i modelli disponibili e i rispettivi workflow"
  echo "  --dry-run          Mostra i comandi cURL generati senza effettuare chiamate di rete"
  echo "  --model <nome>     Esegue il dispatch per uno specifico modello (dwd, dwdicon, meteofrance, ncep, ecmwf, index)"
  echo "  --all              Esegue il dispatch per tutti i modelli (richiede GH_TOKEN o GITHUB_TOKEN)"
  echo "  --force            Imposta force_update: true nel payload"
  echo ""
  echo "Esempio:"
  echo "  export GH_TOKEN=\"ghp_xxxxxxxxxxxx\""
  echo "  $0 --model dwd"
  echo "  $0 --dry-run"
}

DRY_RUN=false
MODEL_TARGET=""
DISPATCH_ALL=false
FORCE_UPDATE=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --list)
      echo "=== Modelli Previsionali MeteoRisk Light ==="
      for key in "${!WORKFLOWS[@]}"; do
        printf "  %-12s -> %-28s (%s)\n" "$key" "${WORKFLOWS[$key]}" "${DESCRIPTIONS[$key]}"
      done
      exit 0
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    --model)
      MODEL_TARGET="${2:-}"
      shift 2
      ;;
    --all)
      DISPATCH_ALL=true
      shift
      ;;
    --force)
      FORCE_UPDATE=true
      shift
      ;;
    -h|--help)
      print_usage
      exit 0
      ;;
    *)
      echo "Opzione non riconosciuta: $1"
      print_usage
      exit 1
      ;;
  esac
done

TOKEN="${GH_TOKEN:-${GITHUB_TOKEN:-}}"

dispatch_model() {
  local model_key="$1"
  local workflow_file="${WORKFLOWS[$model_key]}"
  local desc="${DESCRIPTIONS[$model_key]}"
  local url="${API_BASE}/${workflow_file}/dispatches"
  local payload="{\"ref\":\"main\",\"inputs\":{\"force_update\":${FORCE_UPDATE}}}"

  echo "------------------------------------------------------------"
  echo "Modello: ${model_key} (${desc})"
  echo "Target : ${url}"
  echo "Payload: ${payload}"

  if [ "$DRY_RUN" = true ]; then
    echo "Comando cURL per cron-job.org / terminale:"
    echo "curl -X POST \\"
    echo "  -H \"Accept: application/vnd.github+json\" \\"
    echo "  -H \"Authorization: Bearer \${GH_TOKEN}\" \\"
    echo "  -H \"X-GitHub-Api-Version: 2022-11-28\" \\"
    echo "  -H \"User-Agent: MeteoRisk-CronJob/1.0\" \\"
    echo "  -H \"Content-Type: application/json\" \\"
    echo "  \"${url}\" \\"
    echo "  -d '${payload}'"
    echo ""
    return 0
  fi

  if [ -z "$TOKEN" ]; then
    echo "ERRORE: Variabile GH_TOKEN o GITHUB_TOKEN non definita. Esegui export GH_TOKEN=tuo_token prima di avviare il dispatch."
    exit 1
  fi

  echo "Invio richiesta dispatch a GitHub Actions..."
  local http_code
  http_code=$(curl -s -o /tmp/gh_dispatch_resp.txt -w "%{http_code}" -X POST \
    -H "Accept: application/vnd.github+json" \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    -H "User-Agent: MeteoRisk-CronJob/1.0" \
    -H "Content-Type: application/json" \
    "${url}" \
    -d "${payload}")

  if [ "$http_code" = "204" ]; then
    echo "SUCCESSO (HTTP 204 No Content): Workflow avviato con successo!"
  else
    echo "ERRORE (HTTP ${http_code}):"
    cat /tmp/gh_dispatch_resp.txt
    echo ""
  fi
}

if [ "$DISPATCH_ALL" = true ]; then
  for key in "dwd" "dwdicon" "meteofrance" "ncep" "ecmwf" "index"; do
    dispatch_model "$key"
  done
elif [ -n "$MODEL_TARGET" ]; then
  if [[ -z "${WORKFLOWS[$MODEL_TARGET]:-}" ]]; then
    echo "Errore: Modello '$MODEL_TARGET' sconosciuto."
    echo "Modelli validi: dwd, dwdicon, meteofrance, ncep, ecmwf, index"
    exit 1
  fi
  dispatch_model "$MODEL_TARGET"
else
  echo "Nessun modello specificato. Mostro la simulazione dry-run per tutti i modelli:"
  echo ""
  DRY_RUN=true
  for key in "dwd" "dwdicon" "meteofrance" "ncep" "ecmwf" "index"; do
    dispatch_model "$key"
  done
fi
