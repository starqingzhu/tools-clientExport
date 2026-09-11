#!/bin/bash

cd "$(dirname "$0")"
echo "当前目录: $(pwd)"

export WORKSPACE="$(realpath ../../)"
export LUBAN_DLL="${WORKSPACE}/Luban/Luban.dll"
export CONF_ROOT="$(pwd)"
export DATA_OUTPATH="${CONF_ROOT}/json_out"

echo ${DATA_OUTPATH}

# 注：C_Activity.sh 已增加增量检测逻辑，仅在数据有变更时才会调用本脚本，
# 因此 rm -rf 不会再被频繁触发，保留清空行为以避免旧 JSON 残留。
rm -rf ${DATA_OUTPATH}

dotnet "${LUBAN_DLL}" \
    -t client \
    --conf "${CONF_ROOT}/luban.conf" \
    -x code.lineEnding=crlf \
    -d json \
    -x outputDataDir="${DATA_OUTPATH}"
