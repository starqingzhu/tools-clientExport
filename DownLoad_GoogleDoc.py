import os
import json
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from google.oauth2 import service_account
from google.auth.transport.requests import AuthorizedSession

current_dir = os.path.dirname(os.path.abspath(__file__))
service_account_file = os.path.join(current_dir, "fabled-mystery-471102-b3-f875a0864474.json")

SCOPES = ['https://www.googleapis.com/auth/drive.readonly']

DRIVE_API = 'https://www.googleapis.com/drive/v3'
SHEETS_API = 'https://sheets.googleapis.com/v4/spreadsheets'
EXPORT_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

_thread_local = threading.local()
_manifest_lock = threading.Lock()


def _get_session() -> AuthorizedSession:
    if not hasattr(_thread_local, 'session'):
        creds = service_account.Credentials.from_service_account_file(
            service_account_file, scopes=SCOPES)
        _thread_local.session = AuthorizedSession(creds)
    return _thread_local.session


def _manifest_path(output_dir: str) -> str:
    return os.path.join(output_dir, '.download_manifest.json')


def _load_manifest(output_dir: str) -> dict:
    path = _manifest_path(output_dir)
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_manifest(output_dir: str, delta: dict) -> None:
    """将 delta 中的条目合并写入 manifest，加锁防并发覆盖。"""
    path = _manifest_path(output_dir)
    with _manifest_lock:
        existing = {}
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    existing = json.load(f)
            except Exception:
                pass
        existing.update(delta)
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[WARN] 无法写入 manifest: {e}", flush=True)


def _need_download(file_id: str, remote_mtime: str, local_path: str, manifest: dict) -> bool:
    if not os.path.exists(local_path):
        return True
    entry = manifest.get(file_id)
    if not entry:
        return True
    if entry.get('modifiedTime') != remote_mtime:
        return True
    if entry.get('name') != os.path.basename(local_path):
        return True  # 远端重命名
    return False


def _export_one_file(file_id: str, output_path: str, label: str) -> tuple:
    """导出单个 Google Sheets 文件为 xlsx，写入 output_path"""
    t0 = time.time()
    try:
        session = _get_session()
        url = f'{DRIVE_API}/files/{file_id}/export'
        resp = session.get(url, params={'mimeType': EXPORT_MIME}, stream=True)
        resp.raise_for_status()

        size = 0
        with open(output_path, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=1 << 20):  # 1MB chunks
                f.write(chunk)
                size += len(chunk)

        elapsed = time.time() - t0
        print(f'[OK] {label}  {size/1024:.0f}KB  {elapsed:.1f}s', flush=True)
        return True, None
    except Exception as e:
        elapsed = time.time() - t0
        print(f'[ERR] {label}: {e}  ({elapsed:.1f}s)', flush=True)
        return False, str(e)


def download_google_sheets(output_dir: str, sheets_to_download: dict, max_workers: int = 4, incremental: bool = True) -> tuple:
    """
    并发下载多个 Google Sheets 为 xlsx。
    sheets_to_download: {输出文件名: file_id}
    如果 incremental=True，则通过 manifest 跳过未变更的文件。
    """
    try:
        os.makedirs(output_dir, exist_ok=True)
        manifest = _load_manifest(output_dir) if incremental else {}
        manifest_delta = {}

        # 收集任务并获取远程 modifiedTime
        tasks = []  # (file_id, local_path, label, remote_mtime)
        session = _get_session()
        for name, file_id in sheets_to_download.items():
            local_path = os.path.normpath(os.path.join(output_dir, name))
            remote_mtime = ''
            if incremental:
                try:
                    resp = session.get(f'{DRIVE_API}/files/{file_id}',
                                       params={'fields': 'modifiedTime'})
                    resp.raise_for_status()
                    remote_mtime = resp.json().get('modifiedTime', '')
                except Exception as e:
                    print(f"[WARN] 无法获取 {name} 元数据: {e}，将强制下载", flush=True)
                    remote_mtime = ''
            tasks.append((file_id, local_path, name, remote_mtime))

        filtered_tasks = []
        skipped = 0
        for file_id, local_path, label, remote_mtime in tasks:
            if incremental and not _need_download(file_id, remote_mtime, local_path, manifest):
                print(f"[SKIP] {label} 未变更，跳过下载", flush=True)
                manifest_delta[file_id] = {
                    'name': os.path.basename(local_path),
                    'modifiedTime': remote_mtime
                }
                skipped += 1
            else:
                filtered_tasks.append((file_id, local_path, label, remote_mtime))

        errors = []
        downloaded = 0
        if filtered_tasks:
            with ThreadPoolExecutor(max_workers=min(max_workers, len(filtered_tasks))) as pool:
                futs = {}
                for fid, path, label, mtime in filtered_tasks:
                    fut = pool.submit(_export_one_file, fid, path, label)
                    futs[fut] = (fid, path, label, mtime)
                for fut in as_completed(futs):
                    fid, path, label, mtime = futs[fut]
                    ok, err = fut.result()
                    if ok:
                        downloaded += 1
                        manifest_delta[fid] = {
                            'name': os.path.basename(path),
                            'modifiedTime': mtime
                        }
                    else:
                        errors.append(f'{label}: {err}')

        if incremental:
            _save_manifest(output_dir, manifest_delta)

        if errors:
            return False, f'部分文件下载失败: ' + '; '.join(errors)
        if downloaded == 0 and skipped > 0:
            return True, f'全部 {skipped} 个文件均为最新，无需下载'
        return True, f'下载完成！成功: {downloaded} 个，跳过: {skipped} 个'
    except Exception as e:
        return False, f'下载过程中出现错误: {e}'


def download_folder_files(output_dir: str, folder_id: str, file_types=None, max_workers: int = 10, incremental: bool = True) -> tuple:
    """
    并发下载 Google Drive 文件夹内的所有文件。
    如果 incremental=True，则通过 manifest 跳过未变更的文件。
    """
    if file_types is None:
        file_types = [
            'application/vnd.google-apps.spreadsheet',
            'application/vnd.google-apps.document',
        ]

    export_mimes = {
        'application/vnd.google-apps.spreadsheet': EXPORT_MIME,
        'application/vnd.google-apps.document':
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    }
    exts = {
        'application/vnd.google-apps.spreadsheet': '.xlsx',
        'application/vnd.google-apps.document': '.docx',
    }

    try:
        os.makedirs(output_dir, exist_ok=True)
        manifest = _load_manifest(output_dir) if incremental else {}
        manifest_delta = {}
        session = _get_session()

        type_q = ' or '.join(f"mimeType='{t}'" for t in file_types)
        query = (f"'{folder_id}' in parents "
                 f"and mimeType!='application/vnd.google-apps.folder' "
                 f"and trashed=false"
                 + (f" and ({type_q})" if type_q else ""))

        resp = session.get(f'{DRIVE_API}/files',
                           params={'q': query, 'fields': 'files(id,name,mimeType,modifiedTime)', 'pageSize': 1000})
        resp.raise_for_status()
        files = resp.json().get('files', [])

        if not files:
            return True, '文件夹中没有找到可下载的文件'

        tasks = []
        skipped = 0
        for f in files:
            mt = f['mimeType']
            name = f['name']
            fid = f['id']
            remote_mtime = f.get('modifiedTime', '')
            if mt in export_mimes:
                ext = exts[mt]
                fname = name if name.lower().endswith(ext) else name + ext
                local_path = os.path.normpath(os.path.join(output_dir, fname))
                if incremental and not _need_download(fid, remote_mtime, local_path, manifest):
                    print(f"[SKIP] {fname} 未变更，跳过下载", flush=True)
                    manifest_delta[fid] = {'name': fname, 'modifiedTime': remote_mtime}
                    skipped += 1
                else:
                    tasks.append((fid, local_path, fname, remote_mtime))

        if not tasks:
            if incremental:
                _save_manifest(output_dir, manifest_delta)
            return True, f'全部 {len(files)} 个文件均为最新，无需下载'

        print(f'找到 {len(files)} 个文件，其中 {len(tasks)} 个需要下载，开始并发下载...', flush=True)

        downloaded = 0
        failed = 0
        with ThreadPoolExecutor(max_workers=min(max_workers, len(tasks))) as pool:
            futs = {}
            for fid, path, label, mtime in tasks:
                fut = pool.submit(_export_one_file, fid, path, label)
                futs[fut] = (fid, path, label, mtime)
            for fut in as_completed(futs):
                fid, path, label, mtime = futs[fut]
                ok, _ = fut.result()
                if ok:
                    downloaded += 1
                    manifest_delta[fid] = {'name': label, 'modifiedTime': mtime}
                else:
                    failed += 1

        if incremental:
            _save_manifest(output_dir, manifest_delta)

        return True, f'下载完成！成功: {downloaded} 个，跳过: {skipped} 个，失败: {failed} 个'
    except Exception as e:
        return False, f'下载过程中出现错误: {e}'


def download_multiple_sheets(output_dir: str, sheets_to_download: dict, incremental: bool = True) -> tuple:
    """
    从 Google Sheets 文件按 sheet 名提取并保存为 xlsx。
    内部实现：通过 Drive export API 下载完整 xlsx → openpyxl 裁剪只保留目标 sheet → 另存。
    sheets_to_download: {输出文件名: {'file_id': ..., 'sheet_names': [...]}}
    """
    try:
        os.makedirs(output_dir, exist_ok=True)
        manifest = _load_manifest(output_dir) if incremental else {}
        manifest_delta = {}
        session = _get_session()
        skipped = 0
        downloaded = 0

        for base_name, info in sheets_to_download.items():
            file_id = info['file_id']
            target_sheets = set(info.get('sheet_names', []))

            if not target_sheets:
                print(f"[WARN] {base_name} 未指定 sheet_names，将下载完整文件", flush=True)
                ok, msg = _export_one_file(
                    file_id,
                    os.path.normpath(os.path.join(output_dir, base_name)),
                    base_name
                )
                if not ok:
                    return False, msg
                downloaded += 1
                continue

            remote_mtime = ''
            if incremental:
                try:
                    resp = session.get(f'{DRIVE_API}/files/{file_id}',
                                       params={'fields': 'modifiedTime'})
                    resp.raise_for_status()
                    remote_mtime = resp.json().get('modifiedTime', '')
                except Exception as e:
                    print(f"[WARN] 无法获取 {base_name} 元数据: {e}，将强制下载", flush=True)

            output_path = os.path.normpath(os.path.join(output_dir, base_name))

            if incremental and not _need_download(file_id, remote_mtime, output_path, manifest):
                print(f"[SKIP] {base_name} 未变更，跳过下载", flush=True)
                manifest_delta[file_id] = {
                    'name': base_name,
                    'modifiedTime': remote_mtime
                }
                skipped += 1
                continue

            tmp_path = os.path.normpath(os.path.join(output_dir, f'.tmp_{file_id}.xlsx'))
            ok, err = _export_one_file(file_id, tmp_path, f"{base_name}(full)")
            if not ok:
                return False, f'下载 {base_name} 失败: {err}'

            try:
                import openpyxl
                wb = openpyxl.load_workbook(tmp_path)
                available = set(wb.sheetnames)
                removed = []
                for sheet_name in list(wb.sheetnames):
                    if sheet_name not in target_sheets:
                        del wb[sheet_name]
                        removed.append(sheet_name)

                missing = target_sheets - available
                if missing:
                    print(f"[WARN] {base_name} 目标 sheet 不存在: {sorted(missing)}，可用: {sorted(available)}", flush=True)
                    if missing == target_sheets:
                        return False, f"{base_name} 中找不到任何目标 sheet: {sorted(target_sheets)}"

                if removed:
                    print(f"[INFO] {base_name} 移除 sheets: {sorted(removed)}", flush=True)

                wb.save(output_path)
                wb.close()
                print(f"[OK] {base_name} 提取 {sorted(target_sheets - missing)} 完成", flush=True)
            except ImportError:
                return False, f'处理 {base_name} 需要 openpyxl，请安装: pip install openpyxl'
            except Exception as e:
                return False, f'处理 {base_name} 失败: {e}'
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)

            manifest_delta[file_id] = {
                'name': base_name,
                'modifiedTime': remote_mtime
            }
            downloaded += 1

        if incremental:
            _save_manifest(output_dir, manifest_delta)
        if downloaded == 0 and skipped > 0:
            return True, f'全部 {skipped} 个文件均为最新，无需下载'
        return True, f'下载完成！成功: {downloaded} 个，跳过: {skipped} 个'
    except Exception as e:
        return False, f'下载过程中出现错误: {e}'
