"""Git diff summary and lazy per-file diff routes."""

import asyncio
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

_MAX_UNTRACKED_DIFF_FILES = 50
_MAX_UNTRACKED_FILE_BYTES = 256 * 1024
_MAX_UNTRACKED_TOTAL_BYTES = 1024 * 1024
_MAX_SINGLE_FILE_DIFF_CHARS = 512 * 1024


async def _run_git(args: List[str], cwd: str) -> Tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    return (
        proc.returncode or 0,
        out.decode("utf-8", errors="replace"),
        err.decode("utf-8", errors="replace"),
    )


def _build_single_untracked_diff_from_path(
    root_path: Path, rel: str, max_bytes: int = _MAX_UNTRACKED_FILE_BYTES
) -> Optional[Tuple[str, int]]:
    fpath = (root_path / rel).resolve()
    try:
        fpath.relative_to(root_path)
    except ValueError:
        return None
    if not fpath.is_file():
        return None

    try:
        st = fpath.stat()
        mode = "100755" if (st.st_mode & 0o111) else "100644"
        size = st.st_size
    except OSError:
        return None

    header = [
        f"diff --git a/{rel} b/{rel}",
        f"new file mode {mode}",
    ]

    if size > max_bytes:
        header.append(f"Binary files /dev/null and b/{rel} differ")
        return ("\n".join(header), 0)

    try:
        raw = fpath.read_bytes()
    except OSError:
        return None

    if b"\x00" in raw[:8192]:
        header.append(f"Binary files /dev/null and b/{rel} differ")
        return ("\n".join(header), 0)

    header.append("--- /dev/null")
    header.append(f"+++ b/{rel}")
    if raw:
        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()
        if lines:
            header.append(f"@@ -0,0 +1,{len(lines)} @@")
            header.extend(f"+{line}" for line in lines)
            if not text.endswith("\n"):
                header.append("\\ No newline at end of file")
    return ("\n".join(header), len(raw))


async def _build_untracked_diff(repo_root: str) -> str:
    code, out, _ = await _run_git(["ls-files", "--others", "--exclude-standard", "-z"], repo_root)
    if code != 0 or not out:
        return ""
    rel_paths = [p for p in out.split("\x00") if p]
    if not rel_paths:
        return ""

    root_path = Path(repo_root).resolve()
    chunks: List[str] = []
    total_bytes = 0

    for rel in rel_paths[:_MAX_UNTRACKED_DIFF_FILES]:
        remaining = _MAX_UNTRACKED_TOTAL_BYTES - total_bytes
        if remaining <= 0:
            header = [
                f"diff --git a/{rel} b/{rel}",
                "new file mode 100644",
                f"Binary files /dev/null and b/{rel} differ",
            ]
            chunks.append("\n".join(header))
            continue
        res = _build_single_untracked_diff_from_path(
            root_path, rel, max_bytes=min(_MAX_UNTRACKED_FILE_BYTES, remaining)
        )
        if res is None:
            continue
        chunk, used_bytes = res
        total_bytes += used_bytes
        chunks.append(chunk)

    return "\n".join(chunks)


def _parse_git_name_status_z(out: str) -> Dict[str, Dict[str, str]]:
    """Parse `git diff --name-status -z` output into a map keyed by current path."""
    tokens = out.split("\x00")
    result: Dict[str, Dict[str, str]] = {}
    i = 0
    n = len(tokens)
    while i < n:
        status_raw = tokens[i]
        i += 1
        if not status_raw:
            continue
        code = status_raw[0]
        if code in ("R", "C"):
            if i + 1 >= n:
                break
            old_path = tokens[i]
            new_path = tokens[i + 1]
            i += 2
            result[new_path] = {
                "status": "R" if code == "R" else "A",
                "rename_from": old_path,
                "rename_to": new_path,
            }
        else:
            if i >= n:
                break
            path = tokens[i]
            i += 1
            mapped = code if code in ("A", "D", "M", "R") else "M"
            result[path] = {
                "status": mapped,
                "rename_from": "",
                "rename_to": "",
            }
    return result


def _parse_git_numstat_z(
    numstat_out: str,
    name_status_map: Dict[str, Dict[str, str]],
    scope: str,
) -> List[Dict[str, Any]]:
    """Parse `git diff --numstat -z` combined with `--name-status -z` metadata."""
    tokens = numstat_out.split("\x00")
    files: List[Dict[str, Any]] = []
    seen: set = set()
    i = 0
    n = len(tokens)
    while i < n:
        entry = tokens[i]
        i += 1
        if not entry:
            continue
        parts = entry.split("\t", 2)
        if len(parts) < 3:
            continue
        add_s, del_s, path_part = parts
        rename_from = ""
        rename_to = ""
        if path_part == "":
            if i + 1 >= n:
                break
            rename_from = tokens[i]
            rename_to = tokens[i + 1]
            i += 2
            path = rename_to
        else:
            path = path_part

        is_binary = add_s == "-" or del_s == "-"
        added = 0 if is_binary else int(add_s) if add_s.isdigit() else 0
        deleted = 0 if is_binary else int(del_s) if del_s.isdigit() else 0
        meta = name_status_map.get(path, {})
        status = meta.get("status") or ("R" if rename_from else "M")
        r_from = rename_from or meta.get("rename_from", "")
        r_to = rename_to or meta.get("rename_to", "")
        display_path = f"{r_from} → {r_to}" if (status == "R" and r_from and r_to) else path
        seen.add(path)
        files.append(
            {
                "path": path,
                "display_path": display_path,
                "status": status,
                "added": added,
                "deleted": deleted,
                "is_binary": is_binary,
                "rename_from": r_from,
                "rename_to": r_to,
                "scope": scope,
            }
        )

    for path, meta in name_status_map.items():
        if path in seen:
            continue
        status = meta.get("status", "M")
        r_from = meta.get("rename_from", "")
        r_to = meta.get("rename_to", "")
        display_path = f"{r_from} → {r_to}" if (status == "R" and r_from and r_to) else path
        files.append(
            {
                "path": path,
                "display_path": display_path,
                "status": status,
                "added": 0,
                "deleted": 0,
                "is_binary": False,
                "rename_from": r_from,
                "rename_to": r_to,
                "scope": scope,
            }
        )
    return files


async def _list_untracked_files(repo_root: str) -> List[Dict[str, Any]]:
    code, out, _ = await _run_git(["ls-files", "--others", "--exclude-standard", "-z"], repo_root)
    if code != 0 or not out:
        return []
    root_path = Path(repo_root).resolve()
    items: List[Dict[str, Any]] = []
    for rel in out.split("\x00"):
        if not rel:
            continue
        fpath = (root_path / rel).resolve()
        try:
            fpath.relative_to(root_path)
            st = fpath.stat()
        except (ValueError, OSError):
            continue
        if not fpath.is_file():
            continue
        items.append(
            {
                "path": rel,
                "display_path": rel,
                "status": "A",
                "added": 0,
                "deleted": 0,
                "is_binary": False,
                "rename_from": "",
                "rename_to": "",
                "scope": "untracked",
                "size": st.st_size,
            }
        )
    return items


def _validate_repo_rel_path(repo_root: str, rel_path: str) -> str:
    cleaned = (rel_path or "").strip()
    if not cleaned or os.path.isabs(cleaned):
        raise HTTPException(status_code=400, detail="Invalid relative file path")
    root_path = Path(repo_root).resolve()
    candidate = (root_path / cleaned).resolve()
    try:
        candidate.relative_to(root_path)
    except ValueError:
        raise HTTPException(status_code=400, detail="File path escapes repository root")
    return cleaned


def build_git_diff_router(
    *,
    require_http_auth: Callable[..., Any],
    get_manager: Callable[[], Any],
    get_store: Callable[[], Any],
) -> Tuple[APIRouter, Dict[str, Any]]:
    router = APIRouter()

    def _resolve_diff_workdir(
        workdir: Optional[str],
        profile: Optional[str],
        session_id: Optional[str],
    ) -> str:
        if workdir and os.path.isdir(os.path.abspath(os.path.expanduser(workdir))):
            return os.path.abspath(os.path.expanduser(workdir))
        pool = get_manager()
        try:
            mgr = pool.get_manager(profile)
        except Exception:
            mgr = pool.active
        if session_id:
            rt = mgr._runtimes.get(session_id)
            if rt and rt.workdir:
                return rt.workdir
            row = get_store().get_session(session_id)
            if row and row.get("workdir"):
                return row["workdir"]
        return mgr.workdir

    @router.get("/api/diff")
    async def get_git_diff(
        workdir: Optional[str] = None,
        profile: Optional[str] = None,
        session_id: Optional[str] = None,
        _token: str = Depends(require_http_auth),
    ):
        target_workdir = _resolve_diff_workdir(workdir, profile, session_id)
        code, top_out, _ = await _run_git(["rev-parse", "--show-toplevel"], target_workdir)
        if code != 0:
            return JSONResponse(
                {
                    "is_git_repo": False,
                    "workdir": target_workdir,
                    "branch": "",
                    "status": "",
                    "staged_files": [],
                    "working_files": [],
                    "lazy": True,
                    "message": "当前工作目录不是 Git 仓库",
                }
            )

        repo_root = top_out.strip()
        (
            (_, branch, _),
            (_, status_txt, _),
            (_, work_ns, _),
            (_, work_num, _),
            (_, staged_ns, _),
            (_, staged_num, _),
            untracked_files,
        ) = await asyncio.gather(
            _run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo_root),
            _run_git(["status", "--short"], repo_root),
            _run_git(["diff", "--name-status", "-z"], repo_root),
            _run_git(["diff", "--numstat", "-z"], repo_root),
            _run_git(["diff", "--cached", "--name-status", "-z"], repo_root),
            _run_git(["diff", "--cached", "--numstat", "-z"], repo_root),
            _list_untracked_files(repo_root),
        )

        staged_files = _parse_git_numstat_z(
            staged_num, _parse_git_name_status_z(staged_ns), scope="staged"
        )
        working_files = _parse_git_numstat_z(
            work_num, _parse_git_name_status_z(work_ns), scope="working"
        )
        working_files.extend(untracked_files)

        return {
            "is_git_repo": True,
            "workdir": repo_root,
            "branch": branch.strip(),
            "status": status_txt,
            "staged_files": staged_files,
            "working_files": working_files,
            "lazy": True,
        }

    @router.get("/api/diff/file")
    async def get_git_diff_file(
        path: str,
        scope: str = "working",
        rename_from: Optional[str] = None,
        workdir: Optional[str] = None,
        profile: Optional[str] = None,
        session_id: Optional[str] = None,
        _token: str = Depends(require_http_auth),
    ):
        if scope not in ("working", "staged", "untracked"):
            raise HTTPException(status_code=400, detail="Invalid diff scope")

        target_workdir = _resolve_diff_workdir(workdir, profile, session_id)
        code, top_out, _ = await _run_git(["rev-parse", "--show-toplevel"], target_workdir)
        if code != 0:
            raise HTTPException(status_code=400, detail="当前工作目录不是 Git 仓库")

        repo_root = top_out.strip()
        rel_path = _validate_repo_rel_path(repo_root, path)
        rel_from = _validate_repo_rel_path(repo_root, rename_from) if (rename_from or "").strip() else ""

        diff_txt = ""
        if scope == "untracked":
            res = _build_single_untracked_diff_from_path(Path(repo_root).resolve(), rel_path)
            diff_txt = res[0] if res is not None else ""
        else:
            args = ["diff"]
            if scope == "staged":
                args.append("--cached")
            if rel_from:
                args.extend(["-M", "--", rel_from, rel_path])
            else:
                args.extend(["--", rel_path])
            _, diff_txt, _ = await _run_git(args, repo_root)

        total_chars = len(diff_txt)
        truncated = False
        if total_chars > _MAX_SINGLE_FILE_DIFF_CHARS:
            cut = diff_txt.rfind("\n", 0, _MAX_SINGLE_FILE_DIFF_CHARS)
            if cut <= 0:
                cut = _MAX_SINGLE_FILE_DIFF_CHARS
            diff_txt = diff_txt[:cut]
            truncated = True

        return {
            "path": rel_path,
            "scope": scope,
            "diff": diff_txt,
            "truncated": truncated,
            "total_chars": total_chars,
        }

    return router, {
        "get_git_diff": get_git_diff,
        "get_git_diff_file": get_git_diff_file,
        "_resolve_diff_workdir": _resolve_diff_workdir,
    }
