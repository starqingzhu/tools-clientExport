#! /bin/bash
cd "$(dirname "$0")"
echo "当前目录: $(pwd)"

WORKSPACE="$(realpath ../)"

python3 <<'PYEOF'
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from DownLoad_GoogleDoc import download_google_sheets, download_folder_files, download_multiple_sheets

current_dir = os.getcwd()
output_dir = os.path.join(current_dir, 'Datas')

SHEETS_TO_DOWNLOAD = {
    '__enums__.xlsx': '1dm9FYjk556K_3wg6vAGbHwpQLfcDMaUXvuyKgnAeA4Q',
    '__beans__.xlsx': '1HYkOKp6UOervBs-RsVza0dzAomysOkJAK2mOkx57e_Y',
}

MULTIPLE_SHEETS_TO_DOWNLOAD = {
    '__tables__.xlsx': {
        'file_id': '1D0LEz9-ifodVCdertjGlZ3NMwKG7IfdWpAkyZuAPjrI',
        'sheet_names': ['ActivityConfig']
    }
}

FOLDER_ID = '1qyrGTaYpp194mahPttGf6ZXfAaF0_vP_'

t_start = time.time()

# 三路任务全并发：sheets/multi/folder 目标文件名完全不重叠，无写冲突
tasks = {
    'sheets': lambda: download_google_sheets(output_dir, SHEETS_TO_DOWNLOAD, incremental=True),
    'multi':  lambda: download_multiple_sheets(output_dir, MULTIPLE_SHEETS_TO_DOWNLOAD, incremental=True),
    'folder': lambda: download_folder_files(
        output_dir, FOLDER_ID,
        file_types=['application/vnd.google-apps.spreadsheet'],
        incremental=True
    ),
}

results = {}
any_change = False
with ThreadPoolExecutor(max_workers=3) as pool:
    future_map = {pool.submit(fn): name for name, fn in tasks.items()}
    for fut in as_completed(future_map):
        name = future_map[fut]
        try:
            success, message = fut.result()
            results[name] = (success, message)
            print(f"[{name}] {'成功' if success else '失败'}: {message}")
            # 只要消息里不含“均为最新”，就说明有文件被实际下载/更新
            if success and '均为最新' not in message:
                any_change = True
        except Exception as e:
            results[name] = (False, str(e))
            print(f"[{name}] 异常: {e}")

elapsed = time.time() - t_start
print(f"\n下载阶段总耗时: {elapsed:.1f}s")

if not all(ok for ok, _ in results.values()):
    failed = [n for n, (ok, _) in results.items() if not ok]
    raise SystemExit(f"以下任务失败，终止后续步骤: {failed}")

# 写入标记文件供 shell 判断是否跳过 Luban
skip_flag = os.path.join(output_dir, '.skip_luban')
if not any_change:
    print("所有文件均为最新，标记跳过 Luban 生成阶段")
    with open(skip_flag, 'w') as f:
        pass
else:
    if os.path.exists(skip_flag):
        os.remove(skip_flag)
PYEOF

PYTHON_EXIT=$?
if [ $PYTHON_EXIT -ne 0 ]; then
    echo "❌ 下载阶段失败（exit code: $PYTHON_EXIT），中止执行"
    exit 1
fi

if [ -f "Datas/.skip_luban" ]; then
    echo "⏭️ 检测到所有数据文件均为最新，跳过 Luban 生成阶段"
    rm -f "Datas/.skip_luban"
    exit 0
fi

sh gen_code.sh

echo "✅ 所有操作已完成！"
echo ""
